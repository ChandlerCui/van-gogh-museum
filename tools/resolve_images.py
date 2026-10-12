#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
梵高线上博物馆 · 图像来源解析器
================================
读取 content/manifest.json，为每幅作品寻找"经过实际网络验证"的图像来源，并写出
content/images.json 与 tools/resolve-report.txt。

网络事实（本沙箱）：
  · wikimedia.org 全域不可直连；唯一已验证通道是 wsrv.nl 图像代理访问 Commons 的
    Special:FilePath：
    https://wsrv.nl/?url=<encodeURIComponent(https://commons.wikimedia.org/wiki/Special:FilePath/<文件名>?width=300)>
    —— 文件存在时返回 200 + image/*；不存在时返回 404。
  · 环境变量 HTTPS_PROXY=http://127.0.0.1:18080，urllib/curl 自动走代理。
  · 大都会艺术博物馆 API（collectionapi.metmuseum.org）与图床（images.metmuseum.org）
    可达；旧版 /search 已下线（410），使用 v1.1/search。

验证硬规则：候选必须返回 HTTP 200 且 Content-Type 以 image/ 开头才算命中；
命中后从图像字节解析像素宽高（JPEG SOF / PNG IHDR），失败时改用 wsrv 的
&output=json 兜底。严禁写入未验证条目。

候选文件名来源（按优先级）：
  1. WEBSEARCH_FOUND   —— 第 2 轮由 WebSearch 查得的精确文件名（人工核对后填入）
  2. manifest guesses  —— manifest.json 中每幅作品自带的候选
  3. KNOWN             —— 人工整理的常见命名（含 Google Art Project / Yorck / 荷兰语标题）
  4. 模式生成          —— 标题变体 × 作者前缀 × 文件名模板 批量生成

用法：
  python3 tools/resolve_images.py                # 全量解析并写出两个文件
  python3 tools/resolve_images.py --only a,b,c   # 仅解析指定 id（只打印，不写文件）
  python3 tools/resolve_images.py --met-only     # 仅解析 6 幅 Met 作品（只打印）
"""

import concurrent.futures
import json
import os
import random
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

# ----------------------------------------------------------------------------
# 路径与常量
# ----------------------------------------------------------------------------
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MANIFEST_PATH = os.path.join(ROOT, 'content', 'manifest.json')
IMAGES_OUT = os.path.join(ROOT, 'content', 'images.json')
REPORT_OUT = os.path.join(ROOT, 'tools', 'resolve-report.txt')

# 负结果缓存（404 是确定性结果，不进入任何输出；存 /tmp 不触碰项目目录）
NEG_CACHE_PATH = '/tmp/vangogh_negatives.json'

PROXY = os.environ.get('HTTPS_PROXY') or 'http://127.0.0.1:18080'
PROXY_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({'http': PROXY, 'https': PROXY}))
DIRECT_OPENER = urllib.request.build_opener(
    urllib.request.ProxyHandler({}))  # 显式清空，绕过环境变量代理
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) vangogh-museum-resolver/1.0'}

COMMONS_PATH = 'https://commons.wikimedia.org/wiki/Special:FilePath/'
WSRV = 'https://wsrv.nl/?url='
VERIFY_WIDTH = 300
THREADS = 8

# ----------------------------------------------------------------------------
# 第 2 轮补充：WebSearch 查得并人工核对过的精确 Commons 文件名
# （优先级最高；仍需通过 wsrv 实际验证才会写入输出）
# ----------------------------------------------------------------------------
WEBSEARCH_FOUND = {
    # 由 DBpedia/SPARQL/vg_index（Wikipedia 文章图）查得并人工核对过的精确文件名；
    # 优先级最高，仍需通过 wsrv 实际验证才会写入输出。
    # ---------- 荷兰时期 ----------
    'p1-congregation': [
        'Van_Gogh_-_Die_Kirche_von_Nuenen_mit_Kirchgängern.jpeg',
    ],
    'p1-parsonage-garden': [
        'Van_Gogh_-_The_Parsonage_Garden_at_Nuenen.jpg',
        'De_pastorie_te_Nuenen_-_s0140V1962_-_Van_Gogh_Museum.jpg',
        'WLANL_-_artanonymous_-_De_pastorie_te_Nuenen.jpg',
    ],
    'p1-weaver': [
        'Vincent_van_Gogh_-_Weaver_-_Google_Art_Project.jpg',
        'Van_gogh_weaver_standing_in_front_of_a_loomf33.jpg',
        'Vincent_van_Gogh_-_Weaver_Facing_Right_(1884_Feb.).jpg',
        "Vincent_van_Gogh_-_A_Weaver's_Cottage_-_Google_Art_Project.jpg",
    ],
    'p1-watermill': [
        'Vincent_van_Gogh_-_Watermolen_in_Gennep.jpg',
        'Van_Gogh_-_Wassermühle_in_Gennep2.jpeg',
        'F1144a-Watermolen-te-Gennep.jpg',
        'Van_Gogh_-_Wassermühle_in_Gennep3.jpeg',
    ],
    'p1-old-tower': [
        "De_oude_kerktoren_te_Nuenen_(`Het_boerenkerkhof')_-_s0002V1962_-_Van_Gogh_Museum.jpg",
        'Van_Gogh_-_Der_alte_Friedhofsturm_in_Nuenen.jpeg',
        'Vincent_van_Gogh_-_The_Old_Tower_at_Dusk_-_F40_JH507.jpg',
    ],
    'p1-lane-poplars': [
        'Vincent_van_Gogh_-_Poplars_near_Nuenen_-_Google_Art_Project.jpg',
        'Van_Gogh_-Pappelallee_bei_Sonnenuntergang.jpeg',
        'Vincent_van_Gogh_-_Avenue_of_poplars_in_autumn_-_Google_Art_Project.jpg',
    ],
    'p1-potato-eaters': [
        'Van-willem-vincent-gogh-die-kartoffelesser-03850.jpg',
        'The_Potato_Eaters_-_Vincent_van_Gogh_-_.jpg',
    ],
    'p1-bible': [
        'Stilleven_met_bijbel_-_s0008V1962_-_Van_Gogh_Museum.jpg',
        'WLANL_-_artanonymous_-_Stilleven_met_bijbel.jpg',
    ],
    # ---------- 巴黎时期 ----------
    'p2-agostina': [
        'Portret_van_Agostina_Segatori_-_s0093V1962_-_Van_Gogh_Museum.jpg',
        'Vincent_van_Gogh_-_In_the_café_-_Agostina_Segatori_in_Le_Tambourin_-_Google_Art_Project_2.jpg',
    ],
    'p2-self-grey-felt': [
        'Zelfportret_met_grijze_vilthoed_-_s0016V1962_-_Van_Gogh_Museum.jpg',
        'Van_Gogh_Self-Portrait_with_Grey_Felt_Hat_1886-87_Rijksmuseum.jpg',
    ],
    'p2-tanguy': [
        'Van_Gogh_-_Portrait_of_Pere_Tanguy_1887-8.jpg',
        'Van_Gogh_-_Bildnis_Père_Tanguy.jpeg',
        'Van_Gogh_-_Bildnis_Père_Tanguy1.jpeg',
    ],
    'p2-skull': [
        'Kop_van_een_skelet_met_brandende_sigaret_-_s0083V1962_-_Van_Gogh_Museum.jpg',
        'Vincent_van_Gogh_-_Head_of_a_skeleton_with_a_burning_cigarette_-_Google_Art_Project.jpg',
    ],
    'p2-apples-pears': [
        'Vincent_van_Gogh_-_Kweeperen,_citroenen,_peren_en_druiven_-_Google_Art_Project.jpg',
        'Vincent_van_Gogh_-_Grapes,_Lemons,_Pears,_and_Apples_-_1949.215_-_Art_Institute_of_Chicago.jpg',
    ],
    'p2-red-cabbage': [
        'Rode_kolen_en_uien_-_s0082V1962_-_Van_Gogh_Museum.jpg',
    ],
    'p2-lark': [
        'Van_Gogh_-_Getreidefeld_mit_Mohnblumen_und_Lerche.jpeg',
    ],
    'p2-plum-orchard': [
        'Vincent_van_Gogh_-_Bloeiende_pruimenboomgaard-_naar_Hiroshige_-_Google_Art_Project.jpg',
    ],
    'p2-bridge-rain': [
        'Vincent_van_Gogh_-_Brug_in_de_regen-_naar_Hiroshige_-_Google_Art_Project.jpg',
    ],
    'p2-courtesan': [
        'Vincent_van_Gogh_-_Courtesan-_after_Eisen_-_Google_Art_Project.jpg',
    ],
    # ---------- 阿尔勒时期 ----------
    'p3-sunflowers-amsterdam': [
        'Vincent_van_Gogh_-_Sunflowers_-_VGM_F458.jpg',
        'Van_Gogh_Vase_with_Fifteen_Sunflowers.jpg',
        'Vincent_Willem_van_Gogh_123.jpg',
    ],
    'p3-night-cafe': [
        'Le_café_de_nuit_(The_Night_Café)_by_Vincent_van_Gogh.jpeg',
        'Van_Gogh_-_Das_Nachtcafé_in_Arles.jpeg',
        'Night_Cafe_in_Arles,_The.jpg',
        'Vincent_Willem_van_Gogh_076.jpg',
    ],
    'p3-cafe-terrace': [
        'Vincent_van_Gogh_(1853-1890)_Caféterras_bij_nacht_(place_du_Forum)_Kröller-Müller_Museum_Otterlo_23-8-2016_13-35-40.jpg',
        'Vincent_van_Gogh_-_Cafe_Terrace_at_Night_(1888).jpg',
        'Van_gogh_cafe_arles.jpg',
    ],
    'p3-boch': [
        'Vincent_van_Gogh_-_Eugène_Boch_-_Google_Art_Project.jpg',
        'Van_Gogh_Portrait_Eugene_Boch.jpg',
        'Van_Gogh_-_Portrait_of_Eugéne_Boch.jpg',
    ],
    'p3-poet-garden': [
        "Vincent_van_Gogh_-_The_Poet's_Garden_-_1933.433_-_Art_Institute_of_Chicago.jpg",
        "The_Poet's_Garden_1888_Vincent_van_Gogh.jpg",
    ],
    'p3-white-orchard': [
        'Vincent_van_Gogh_-_The_white_orchard_-_Google_Art_Project.jpg',
    ],
    'p3-orchard-cypresses': [
        'Vincent_van_Gogh_-_Orchard_Bordered_by_Cypresse_-_ILE2017.12.1_-_Yale_University_Art_Gallery.jpg',
        'Vincent_Willem_van_Gogh_077.jpg',
        'Van_Gogh_-_Blühender_Obstgarten,_von_Zypressen_umgeben.jpeg',
    ],
    'p3-langlois': [
        'Vincent_Willem_van_Gogh_-_Pont_de_Langlois_-_Kröller-Müller.jpg',
        'Vincent_van_Gogh_-_De_brug_van_Langlois_-_Google_Art_Project.jpg',
        'Vincent_van_Gogh_-_Bridge_at_Arles_(Pont_de_Langlois)_F397.jpg',
        'Willem_van_Gogh_F1470.jpg',
    ],
    'p3-red-vineyard': [
        'Red_vineyards.jpg',
    ],
    'p3-etten-garden': [
        'Vincent_Willem_van_Gogh_098.jpg',
    ],
    'p3-gauguin-chair': [
        'Vincent_van_Gogh_-_De_stoel_van_Gauguin_-_Google_Art_Project.jpg',
    ],
    'p3-snow': [
        'Van_Gogh_-_Landschaft_im_Schnee.jpg',
    ],
    'p3-fishing-boats': [
        'Vissersboten_op_het_strand_van_Les_Saintes-Maries-de-la-Mer_-_s0028V1962_-_Van_Gogh_Museum.jpg',
        'Vincent_van_Gogh_-_Fishing_Boats_at_Saintes-Maries-de-la-Mer.jpg',
    ],
    'p3-alyscamps': [
        'Van_gogh_lallee_des_alyscamps.jpg',
        'Alyscamps_van_gogh.jpg',
        'Les_Alyscamps.jpg',
        'Van_gogh_alyscamps_other.jpg',
    ],
    'p3-self-gauguin': [
        'Van_Gogh_self-portrait_dedicated_to_Gauguin.jpg',
    ],
    # ---------- 圣雷米时期 ----------
    'p4-good-samaritan': [
        'Van_Gogh_-_Der_barmherzige_Samariter.jpeg',
        'Vincent_Willem_van_Gogh_022.jpg',
    ],
    'p4-prisoners': [
        'Vincent_Willem_van_Gogh_037.jpg',
    ],
    'p4-long-grass': [
        'Van_Gogh_-_Rasenstück_mit_Schmetterlingen.jpeg',
        'Tuin_met_vlinders_-_s0110V1962_-_Van_Gogh_Museum.jpg',
    ],
    'p4-butterflies-poppies': [
        'Vlinders_en_klaprozen_-_s0188V1962_-_Van_Gogh_Museum.jpg',
        'Van_Gogh_-_Klatschmohn_und_Schmetterlinge.jpeg',
    ],
    'p4-lazarus': [
        'De_opwekking_van_Lazarus_(naar_Rembrandt)_-_s0169V1962_-_Van_Gogh_Museum.jpg',
        'Vincent_van_Gogh_-_The_raising_of_Lazarus_(after_Rembrandt)_-_Google_Art_Project.jpg',
    ],
    'p4-almond-blossom': [
        'Vincent_van_Gogh_-_Almond_blossom_-_Google_Art_Project.jpg',
        'Amandelboom_in_bloei_-_s0035V1962_-_Van_Gogh_Museum.jpg',
    ],
    'p4-st-paul-garden': [
        'De_tuin_van_de_inrichting_-_s0196V1962_-_Van_Gogh_Museum.jpg',
        "Tuin_van_de_inrichting_Saint-Paul_('Het_vallen_van_de_bladeren')_-_s0046V1962_-_Van_Gogh_Museum.jpg",
        'Van_Gogh_-_Garten_des_Hospitals_Saint-Paul.jpeg',
    ],
    'p4-binding-sheaves': [
        'De_schovenbindster_(naar_Millet)_-_s0172V1962_-_Van_Gogh_Museum.jpg',
        'Van_Gogh_-_Die_Garbenbinderin_(nach_Millet).jpeg',
    ],
    # ---------- 奥维尔时期 ----------
    'p5-crows': [
        'Korenveld_met_kraaien_-_s0149V1962_-_Van_Gogh_Museum.jpg',
        'Vincent_van_Gogh_-_Wheatfield_with_crows_-_Google_Art_Project.jpg',
        'Vincent_van_Gogh_(1853-1890)_-_Wheat_Field_with_Crows_(1890).jpg',
        'Vincent_Van_Gogh_-_Wheatfield_with_Crows.jpg',
    ],
    'p5-church': [
        'Vincent_van_Gogh_-_The_Church_in_Auvers-sur-Oise,_View_from_the_Chevet_-_Google_Art_Project.jpg',
        'Eglise_Auvers-sur-Oise_FRA_001.jpg',
    ],
    'p5-adeline': [
        'Adeline_Ravoux,_by_Vincent_van_Gogh,_Cleveland_Museum_of_Art,_1958.31.jpg',
        'Van_Gogh_-_Bildnis_Adeline_Ravoux2.jpeg',
    ],
    'p5-thunderclouds': [
        'Korenveld_onder_onweerslucht_-_s0106V1962_-_Van_Gogh_Museum.jpg',
        'Vincent_van_Gogh_-_Wheatfield_under_thunderclouds_-_Google_Art_Project.jpg',
        'Vincent_van_Gogh_-_Wheatfield_Under_Thunderclouds_-_VGM_F778.jpg',
    ],
    'p5-daisies-poppies': [
        'Van_Gogh_-_Vase_mit_Margeriten_und_Anemonen.jpeg',
        'Vincent_van_Gogh_-_Stilleven_met_een_boeket_van_Madeliefjes.jpg',
        'Van_Gogh_-_Vase_mit_rotem_Klatschmohn.jpeg',
    ],
    'p5-marguerite': [
        'Van_Gogh_-_Marguerite_Gachet_am_Klavier.jpeg',
    ],
    'p5-twilight': [
        'Landschap_bij_avondschemering_-_s0107V1962_-_Van_Gogh_Museum.jpg',
    ],
    'p5-sheaves': [
        'Vincent_van_Gogh_-_Sheaves_of_Wheat,_1890.jpg',
        'Van_Gogh_-_Feld_mit_Korngarben.jpeg',
    ],
}

# ----------------------------------------------------------------------------
# 人工整理的常见命名候选（按可能性排序；未命中自动落入模式生成）
# ----------------------------------------------------------------------------
KNOWN = {
    # ---------------- 荷兰时期 ----------------
    'p1-congregation': [
        'Congregation Leaving the Reformed Church at Nuenen.jpg',
        'Van Gogh - Congregation Leaving the Reformed Church at Nuenen - Google Art Project.jpg',
        'Vincent van Gogh - Congregation Leaving the Reformed Church in Nuenen.jpg',
        'De kerkgangers bij de hervormde kerk te Nuenen.jpg',
    ],
    'p1-parsonage-garden': [
        'Vincent van Gogh - The Parsonage Garden at Nuenen in Spring - Google Art Project.jpg',
        'The Parsonage Garden at Nuenen in Spring.jpg',
        'Van Gogh - De pastorie te Nuenen in het voorjaar - schilderij.jpg',
        'De pastorie in Nuenen in het voorjaar.jpg',
    ],
    'p1-weaver': [
        'Vincent van Gogh - Weaver in an Interior - Google Art Project.jpg',
        'Van Gogh - Weaver in an Interior - Google Art Project.jpg',
        'Weaver in an Interior.jpg',
    ],
    'p1-watermill': [
        'Vincent van Gogh - The Water Mill at Gennep - Google Art Project.jpg',
        'Van Gogh - The Water Mill at Gennep - Google Art Project.jpg',
        'The Water Mill at Gennep.jpg',
        'Van Gogh - De watermolen te Gennep - schilderij.jpg',
    ],
    'p1-old-tower': [
        'Van Gogh - The Old Tower of Nuenen - Google Art Project.jpg',
        'The Old Tower of Nuenen.jpg',
        'Van Gogh - De oude toren te Nuenen - schilderij.jpg',
    ],
    'p1-lane-poplars': [
        'Van Gogh - Lane with Poplars near Nuenen - Google Art Project.jpg',
        'Lane with Poplars near Nuenen.jpg',
        'Van Gogh - Laan met populieren bij Nuenen - schilderij.jpg',
    ],
    # ---------------- 巴黎时期 ----------------
    'p2-moulin-galette': [
        'Vincent van Gogh - Le Moulin de la Galette - Google Art Project.jpg',
        'Van Gogh - Le Moulin de la Galette - Google Art Project.jpg',
        'Le Moulin de la Galette.jpg',
        'Le moulin de la galette.jpg',
    ],
    'p2-agostina': [
        'Vincent van Gogh - Agostina Segatori in the Café du Tambourin - Google Art Project.jpg',
        'Van Gogh - Agostina Segatori in the Café du Tambourin - Google Art Project.jpg',
        'Agostina Segatori in the Café du Tambourin.jpg',
        'Agostina Segatori in the Cafe du Tambourin.jpg',
    ],
    'p2-boulevard-clichy': [
        'Vincent van Gogh - Boulevard de Clichy - Google Art Project.jpg',
        'Van Gogh - Boulevard de Clichy - Google Art Project.jpg',
        'Boulevard de Clichy.jpg',
    ],
    'p2-self-grey-felt': [
        'Vincent van Gogh - Self-Portrait with Grey Felt Hat - Google Art Project.jpg',
        'Van Gogh - Self-Portrait with Grey Felt Hat - Google Art Project.jpg',
        'Self-Portrait with Grey Felt Hat.jpg',
        'Van Gogh - Zelfportret - Google Art Project.jpg',
        'Vincent van Gogh - Zelfportret met grijze vilthoed - schilderij.jpg',
    ],
    'p2-self-aic': [
        'Self-Portrait, 1887, Vincent van Gogh.jpg',
        'Vincent van Gogh - Self-Portrait - 1887 - Google Art Project.jpg',
    ],
    'p2-self-orsay-87': [
        'Vincent van Gogh - Self-Portrait - Google Art Project.jpg',
        'Van Gogh - Self-Portrait - Google Art Project.jpg',
    ],
    'p2-tanguy': [
        'Vincent van Gogh - Portrait of Père Tanguy - Google Art Project.jpg',
        'Van Gogh - Portrait of Père Tanguy - Google Art Project.jpg',
        'Père Tanguy.jpg',
        'Pere Tanguy.jpg',
        'Portrait of Père Tanguy by Vincent Van Gogh.jpeg',
    ],
    'p2-skull': [
        'Van Gogh - Skull of a Skeleton with Burning Cigarette - Google Art Project.jpg',
        'Vincent van Gogh - Skull of a Skeleton with Burning Cigarette - Google Art Project.jpg',
        'Skull of a Skeleton with Burning Cigarette.jpg',
        'Vincent van Gogh - Kop van een skelet met brandende sigaret - schilderij.jpg',
    ],
    'p2-apples-pears': [
        'Van Gogh - Still Life with Apples, Pears, Lemons and Grapes - Google Art Project.jpg',
        'Vincent van Gogh - Still Life with Apples, Pears, Lemons and Grapes - Google Art Project.jpg',
        'Still Life with Apples, Pears, Lemons and Grapes.jpg',
    ],
    'p2-red-cabbage': [
        'Van Gogh - Still Life with Red Cabbages and Onions - Google Art Project.jpg',
        'Van Gogh - Still Life with Red Cabbage and Onions - Google Art Project.jpg',
        'Still Life with Red Cabbages and Onions.jpg',
    ],
    'p2-lark': [
        'Vincent van Gogh - Wheat Field with a Lark - Google Art Project.jpg',
        'Van Gogh - Wheat Field with a Lark - Google Art Project.jpg',
        'Wheat Field with a Lark.jpg',
        'Wheatfield with Larks.jpg',
    ],
    'p2-plum-orchard': [
        'Van Gogh - Flowering Plum Orchard - Google Art Project.jpg',
        'Vincent van Gogh - Flowering Plum Orchard - Google Art Project.jpg',
        'Van Gogh - Flowering plum orchard (after Hiroshige) - Google Art Project.jpg',
        'Flowering Plum Orchard (after Hiroshige).jpg',
    ],
    'p2-bridge-rain': [
        'Van Gogh - The Bridge in the Rain - Google Art Project.jpg',
        'Vincent van Gogh - The Bridge in the Rain (after Hiroshige) - Google Art Project.jpg',
        'Van Gogh - Bridge in the Rain (after Hiroshige) - Google Art Project.jpg',
        'Bridge in the Rain (after Hiroshige).jpg',
    ],
    'p2-courtesan': [
        'Van Gogh - The Courtesan - Google Art Project.jpg',
        'Van Gogh - The Courtesan (after Eisen) - Google Art Project.jpg',
        'Vincent van Gogh - The Courtesan (after Eisen) - Google Art Project.jpg',
        'The Courtesan (after Eisen).jpg',
    ],
    # ---------------- 阿尔勒时期 ----------------
    'p3-sunflowers-london': [
        'Vincent van Gogh - Sunflowers - Google Art Project.jpg',
        'Van Gogh - Sunflowers - Google Art Project.jpg',
        'Sunflowers - Vincent van Gogh.jpg',
        'Vase with Fifteen Sunflowers.jpg',
    ],
    'p3-sunflowers-munich': [
        'Van Gogh - Twelve Sunflowers in a Vase - Google Art Project.jpg',
        'Vincent van Gogh - Twelve Sunflowers in a Vase - Google Art Project.jpg',
        'Van Gogh - Vase with Twelve Sunflowers - Google Art Project.jpg',
        'Vase with Twelve Sunflowers.jpg',
        'Twelve Sunflowers in a Vase.jpg',
    ],
    'p3-sunflowers-amsterdam': [
        'Van Gogh - Vase with Fifteen Sunflowers - Google Art Project.jpg',
        'Vincent van Gogh - Vase with Fifteen Sunflowers - Google Art Project.jpg',
        'Van Gogh - Zonnebloemen - Google Art Project.jpg',
        'Vase with Fifteen Sunflowers - Van Gogh Museum.jpg',
    ],
    'p3-night-cafe': [
        'Van Gogh - The Night Café - Google Art Project.jpg',
        'Vincent van Gogh - The Night Café - Google Art Project.jpg',
        'Van Gogh - The Night Cafe - Google Art Project.jpg',
        'The Night Café.jpg',
        'Le café de nuit (Van Gogh).jpg',
    ],
    'p3-cafe-terrace': [
        'Vincent van Gogh - The Café Terrace on the Place du Forum, Arles, at Night - Google Art Project.jpg',
        'Vincent van Gogh - Café Terrace at Night - Google Art Project.jpg',
        'Van Gogh - Café Terrace at Night (Yorck).jpg',
        'Vincent van Gogh - Café Terrace at Night (Yorck).jpg',
        'Café Terrace at Night.jpg',
    ],
    'p3-yellow-house': [
        'Van Gogh - The Yellow House (The Street) - Google Art Project.jpg',
        'Vincent van Gogh - The Yellow House (The Street) - Google Art Project.jpg',
        'Van Gogh - Het gele huis - Google Art Project.jpg',
        'The Yellow House (The Street).jpg',
        'Vincent van Gogh - The Yellow House - Google Art Project.jpg',
    ],
    'p3-bedroom': [
        'Van Gogh - De slaapkamer - Google Art Project.jpg',
        'Vincent van Gogh - De slaapkamer - Google Art Project.jpg',
        'Van Gogh - Bedroom in Arles - Google Art Project.jpg',
        'Bedroom in Arles.jpg',
        'Vincent van Gogh - The Bedroom - Google Art Project.jpg',
    ],
    'p3-rhone': [
        'Van Gogh - Starry Night over the Rhône - Google Art Project.jpg',
        'Vincent van Gogh - Starry Night Over the Rhône - Google Art Project.jpg',
        'Van Gogh - Starry Night Over the Rhone - Google Art Project.jpg',
        'Starry Night Over the Rhône.jpg',
        'Starry Night over the Rhone.jpg',
    ],
    'p3-bandaged-ear': [
        'Van Gogh - Self-Portrait with Bandaged Ear - Google Art Project.jpg',
        'Vincent van Gogh - Self-Portrait with Bandaged Ear - Google Art Project.jpg',
        'Self-Portrait with Bandaged Ear.jpg',
        'Van Gogh - Zelfportret met verbonden oor - schilderij.jpg',
    ],
    'p3-boch': [
        'Van Gogh - Portrait of Eugène Boch - Google Art Project.jpg',
        'Vincent van Gogh - Portrait of Eugène Boch - Google Art Project.jpg',
        'Van Gogh - Portrait of Eugene Boch - Google Art Project.jpg',
        'Portrait of Eugène Boch.jpg',
    ],
    'p3-ginoux': [
        "Van Gogh - L'Arlésienne - Google Art Project.jpg",
        "Vincent van Gogh - L'Arlésienne (Madame Ginoux) - Google Art Project.jpg",
        "L'Arlésienne, Madame Ginoux.jpg",
        "Van Gogh - L'Arlésienne (Madame Joseph-Michel Ginoux) - Google Art Project.jpg",
    ],
    'p3-roulin': [
        'Vincent van Gogh - The Postman Joseph Roulin - Google Art Project.jpg',
        'Van Gogh - Portrait of the Postman Joseph Roulin - Google Art Project.jpg',
        'Joseph Roulin.jpg',
        'Portrait of the Postman Joseph Roulin.jpg',
    ],
    'p3-berceuse': [
        'Van Gogh - La Berceuse (Augustine Roulin) - Google Art Project.jpg',
        'Vincent van Gogh - La Berceuse (Augustine Roulin) - Google Art Project.jpg',
        'La Berceuse (Augustine Roulin).jpg',
    ],
    'p3-zouave': [
        'Van Gogh - The Zouave - Google Art Project.jpg',
        'Vincent van Gogh - The Zouave - Google Art Project.jpg',
        'The Zouave.jpg',
        'Van Gogh - De Zouaaf - schilderij.jpg',
    ],
    'p3-sower': [
        'Van Gogh - The Sower - Google Art Project.jpg',
        'Vincent van Gogh - The Sower - Google Art Project.jpg',
        'Van Gogh - De zaaier - Google Art Project.jpg',
    ],
    'p3-harvest': [
        'Van Gogh - The Harvest - Google Art Project.jpg',
        'Vincent van Gogh - The Harvest - Google Art Project.jpg',
        'Van Gogh - De oogst - Google Art Project.jpg',
        'Vincent van Gogh - De oogst - Google Art Project.jpg',
        'The Harvest.jpg',
    ],
    'p3-farmhouse': [
        'Vincent van Gogh - Farmhouse in Provence - Google Art Project.jpg',
        'Van Gogh - Farmhouse in Provence - Google Art Project.jpg',
        'Farmhouse in Provence.jpg',
    ],
    'p3-poet-garden': [
        "Vincent van Gogh - The Poet's Garden - Google Art Project.jpg",
        "Van Gogh - The Poet's Garden - Google Art Project.jpg",
        "The Poet's Garden.jpg",
    ],
    'p3-peach-trees': [
        'Van Gogh - Pink Peach Trees - Google Art Project.jpg',
        'Vincent van Gogh - Pink Peach Trees - Google Art Project.jpg',
        'Pink Peach Trees (Souvenir de Mauve).jpg',
    ],
    'p3-white-orchard': [
        'Van Gogh - The White Orchard - Google Art Project.jpg',
        'Vincent van Gogh - The White Orchard - Google Art Project.jpg',
        'The White Orchard.jpg',
        'Van Gogh - De witte boomgaard - schilderij.jpg',
    ],
    'p3-orchard-cypresses': [
        'Van Gogh - Orchard Bordered by Cypresses - Google Art Project.jpg',
        'Vincent van Gogh - Orchard Bordered by Cypresses - Google Art Project.jpg',
        'Orchard Bordered by Cypresses.jpg',
    ],
    'p3-langlois': [
        'Van Gogh - The Langlois Bridge at Arles - Google Art Project.jpg',
        'Vincent van Gogh - The Langlois Bridge at Arles - Google Art Project.jpg',
        'The Langlois Bridge at Arles.jpg',
        'Van Gogh - De Langloisbrug - schilderij.jpg',
    ],
    'p3-red-vineyard': [
        'Van Gogh - The Red Vineyard - Google Art Project.jpg',
        'Vincent van Gogh - The Red Vineyard - Google Art Project.jpg',
        'The Red Vineyard.jpg',
        'Le vignoble rouge.jpg',
        'Vincent van Gogh - Le Vignoble Rouge.jpg',
    ],
    'p3-etten-garden': [
        'Van Gogh - Memory of the Garden at Etten - Google Art Project.jpg',
        'Vincent van Gogh - Memory of the Garden at Etten - Google Art Project.jpg',
        'Memory of the Garden at Etten.jpg',
    ],
    'p3-dance-hall': [
        'Van Gogh - The Dance Hall in Arles - Google Art Project.jpg',
        'Vincent van Gogh - The Dance Hall in Arles - Google Art Project.jpg',
        'The Dance Hall in Arles.jpg',
    ],
    'p3-vangogh-chair': [
        "Vincent van Gogh - Van Gogh's Chair - Google Art Project.jpg",
        "Van Gogh's Chair.jpg",
        "Van Gogh - De stoel van Van Gogh - schilderij.jpg",
        "Vincent's Chair with Pipe.jpg",
    ],
    'p3-gauguin-chair': [
        "Van Gogh - Gauguin's Chair - Google Art Project.jpg",
        "Vincent van Gogh - Gauguin's Chair - Google Art Project.jpg",
        "Gauguin's Chair.jpg",
        "Van Gogh - De stoel van Gauguin - schilderij.jpg",
    ],
    'p3-snow': [
        'Van Gogh - Landscape with Snow - Google Art Project.jpg',
        'Vincent van Gogh - Landscape with Snow - Google Art Project.jpg',
        'Landscape with Snow.jpg',
    ],
    'p3-fishing-boats': [
        'Van Gogh - Fishing Boats on the Beach at Les Saintes-Maries - Google Art Project.jpg',
        'Vincent van Gogh - Fishing Boats on the Beach at Les Saintes-Maries-de-la-Mer - Google Art Project.jpg',
        'Fishing Boats on the Beach at Les Saintes-Maries.jpg',
    ],
    'p3-alyscamps': [
        'Van Gogh - The Alyscamps - Google Art Project.jpg',
        'Vincent van Gogh - The Alyscamps - Google Art Project.jpg',
        'The Alyscamps.jpg',
    ],
    'p3-self-gauguin': [
        'Vincent van Gogh - Self-Portrait Dedicated to Paul Gauguin - Google Art Project.jpg',
        'Van Gogh - Self-Portrait Dedicated to Paul Gauguin - Google Art Project.jpg',
        'Self-Portrait Dedicated to Paul Gauguin.jpg',
    ],
    # ---------------- 圣雷米时期 ----------------
    'p4-irises': [
        'Van Gogh - Irises - Google Art Project.jpg',
        'Vincent van Gogh - Irises - Google Art Project.jpg',
        'Irises - Vincent van Gogh.jpg',
    ],
    'p4-self-orsay-89': [
        'Vincent van Gogh - Self-Portrait - Google Art Project.jpg',
        'Van Gogh - Self-Portrait - Google Art Project.jpg',
    ],
    'p4-self-nga-89': [
        'Vincent van Gogh - Self-Portrait - 1889 - Google Art Project.jpg',
        'Van Gogh - Self-Portrait - 1889 - Google Art Project.jpg',
        'Self-Portrait, 1889, National Gallery of Art.jpg',
    ],
    'p4-mulberry': [
        'Van Gogh - The Mulberry Tree - Google Art Project.jpg',
        'Vincent van Gogh - The Mulberry Tree - Google Art Project.jpg',
        'The Mulberry Tree.jpg',
    ],
    'p4-peacock-moth': [
        'Van Gogh - The Great Peacock Moth - Google Art Project.jpg',
        'Vincent van Gogh - The Great Peacock Moth - Google Art Project.jpg',
        'The Great Peacock Moth.jpg',
    ],
    'p4-pieta': [
        'Van Gogh - Pietà - Google Art Project.jpg',
        'Van Gogh - Pieta - Google Art Project.jpg',
        'Vincent van Gogh - Pietà - Google Art Project.jpg',
        'Pietà (after Delacroix).jpg',
    ],
    'p4-good-samaritan': [
        'Van Gogh - The Good Samaritan - Google Art Project.jpg',
        'Vincent van Gogh - The Good Samaritan - Google Art Project.jpg',
        'The Good Samaritan (after Delacroix).jpg',
    ],
    'p4-prisoners': [
        'Van Gogh - Prisoners Exercising - Google Art Project.jpg',
        'Vincent van Gogh - Prisoners Exercising (after Doré) - Google Art Project.jpg',
        'Prisoners Exercising.jpg',
        'Prisoners Exercising (after Doré).jpg',
    ],
    'p4-long-grass': [
        'Van Gogh - Long Grass with Butterflies - Google Art Project.jpg',
        'Vincent van Gogh - Long Grass with Butterflies - Google Art Project.jpg',
        'Long Grass with Butterflies.jpg',
    ],
    'p4-butterflies-poppies': [
        'Van Gogh - Butterflies and Poppies - Google Art Project.jpg',
        'Van Gogh - Poppies and Butterflies - Google Art Project.jpg',
        'Butterflies and Poppies.jpg',
    ],
    'p4-eternity-gate': [
        "Van Gogh - At Eternity's Gate - Google Art Project.jpg",
        "Vincent van Gogh - At Eternity's Gate - Google Art Project.jpg",
        "At Eternity's Gate.jpg",
    ],
    'p4-lazarus': [
        'Van Gogh - The Raising of Lazarus - Google Art Project.jpg',
        'Vincent van Gogh - The Raising of Lazarus (after Rembrandt) - Google Art Project.jpg',
        'The Raising of Lazarus (after Rembrandt).jpg',
    ],
    'p4-ginoux-rome': [
        "Van Gogh - L'Arlésienne (Madame Ginoux) - Google Art Project.jpg",
        "L'Arlésienne (Madame Ginoux).jpg",
        "Van Gogh - L'Arlésienne - Google Art Project.jpg",
    ],
    'p4-almond-blossom': [
        'Van Gogh - Almond Blossom - Google Art Project.jpg',
        'Vincent van Gogh - Almond Blossom - Google Art Project.jpg',
        'Van Gogh - Amandelbloesem - Google Art Project.jpg',
        'Almond Blossom.jpg',
    ],
    'p4-st-paul-garden': [
        "Van Gogh - The Garden of Saint-Paul's Hospital - Google Art Project.jpg",
        "Vincent van Gogh - The Garden of Saint-Paul's Hospital - Google Art Project.jpg",
        "The Garden of Saint-Paul's Hospital.jpg",
    ],
    'p4-binding-sheaves': [
        'Van Gogh - Peasant Woman Binding Sheaves (after Millet) - Google Art Project.jpg',
        'Vincent van Gogh - Peasant Woman Binding Sheaves - Google Art Project.jpg',
        'Peasant Woman Binding Sheaves (after Millet).jpg',
    ],
    'p4-sower-millet': [
        'Van Gogh - The Sower (after Millet) - Google Art Project.jpg',
        'The Sower (after Millet).jpg',
        'Van Gogh - De zaaier (naar Millet) - schilderij.jpg',
    ],
    # ---------------- 奥维尔时期 ----------------
    'p5-crows': [
        'Van Gogh - Wheatfield with Crows - Google Art Project.jpg',
        'Vincent van Gogh - Wheatfield with Crows - Google Art Project.jpg',
        'Van Gogh - Korenveld met kraaien - Google Art Project.jpg',
        'Wheatfield with Crows.jpg',
    ],
    'p5-gachet-1': [
        'Van Gogh - Portrait of Dr. Gachet - Google Art Project.jpg',
        'Vincent van Gogh - Portrait of Dr. Gachet - Google Art Project.jpg',
        'Portrait of Dr. Gachet.jpg',
    ],
    'p5-gachet-2': [
        'Van Gogh - Portrait of Dr. Gachet (second version) - Google Art Project.jpg',
        'Vincent van Gogh - Dr. Gachet - Google Art Project.jpg',
        'Portrait of Dr. Gachet (second version).jpg',
    ],
    'p5-church': [
        'Van Gogh - The Church at Auvers - Google Art Project.jpg',
        'Vincent van Gogh - The Church at Auvers-sur-Oise - Google Art Project.jpg',
        'Van Gogh - De kerk te Auvers - schilderij.jpg',
        'The Church at Auvers.jpg',
    ],
    'p5-houses-toledo': [
        'Vincent van Gogh - Houses at Auvers - Google Art Project.jpg',
        'Van Gogh - Houses at Auvers - Google Art Project.jpg',
        'Houses at Auvers.jpg',
    ],
    'p5-cordeville': [
        'Van Gogh - Thatched Cottages at Cordeville - Google Art Project.jpg',
        'Vincent van Gogh - Thatched Cottages at Cordeville - Google Art Project.jpg',
        'Thatched Cottages at Cordeville.jpg',
    ],
    'p5-white-house-night': [
        'Van Gogh - The White House at Night - Google Art Project.jpg',
        'Vincent van Gogh - The White House at Night.jpg',
        'The White House at Night.jpg',
    ],
    'p5-adeline': [
        'Vincent van Gogh - Portrait of Adeline Ravoux - Google Art Project.jpg',
        'Van Gogh - Portrait of Adeline Ravoux - Google Art Project.jpg',
        'Portrait of Adeline Ravoux.jpg',
    ],
    'p5-gachet-garden': [
        "Van Gogh - Doctor Gachet's Garden in Auvers - Google Art Project.jpg",
        "Vincent van Gogh - Le jardin du Dr Gachet à Auvers-sur-Oise - Google Art Project.jpg",
        "Doctor Gachet's Garden in Auvers.jpg",
    ],
    'p5-thunderclouds': [
        'Van Gogh - Wheatfield under Thunderclouds - Google Art Project.jpg',
        'Vincent van Gogh - Wheatfield under Thunderclouds - Google Art Project.jpg',
        'Wheatfield under Thunderclouds.jpg',
    ],
    'p5-tree-roots': [
        'Van Gogh - Tree Roots - Google Art Project.jpg',
        'Vincent van Gogh - Tree Roots - Google Art Project.jpg',
        'Van Gogh - Boomwortels - Google Art Project.jpg',
        'Tree Roots.jpg',
    ],
    'p5-daisies-poppies': [
        'Vincent van Gogh - Vase with Daisies and Poppies - Google Art Project.jpg',
        'Van Gogh - Vase with Daisies and Poppies.jpg',
        'Vase with Daisies and Poppies.jpg',
    ],
    'p5-marguerite': [
        'Van Gogh - Marguerite Gachet at the Piano - Google Art Project.jpg',
        'Vincent van Gogh - Marguerite Gachet at the Piano - Google Art Project.jpg',
        'Marguerite Gachet at the Piano.jpg',
    ],
    'p5-twilight': [
        'Van Gogh - Landscape at Twilight - Google Art Project.jpg',
        'Vincent van Gogh - Landscape at Twilight - Google Art Project.jpg',
        'Landscape at Twilight.jpg',
    ],
    'p5-sheaves': [
        'Van Gogh - Sheaves of Wheat - Google Art Project.jpg',
        'Vincent van Gogh - Sheaves of Wheat - Google Art Project.jpg',
        'Sheaves of Wheat.jpg',
    ],
}

# ----------------------------------------------------------------------------
# 多版本消歧：命中文件名的语义约束（must / not 均为正则，匹配文件名）
# ----------------------------------------------------------------------------
EXPECT = {
    'p3-sunflowers-london':  {'not': r'Twelve|Zonnebloem|Zonneweide'},
    'p3-sunflowers-munich':  {'must': r'Twelve'},
    'p3-sunflowers-amsterdam': {'not': r'Twelve'},
    'p3-rhone':              {'must': r'Rhone|Rhône'},
    'p4-starry-night':       {'must': r'Starry Night', 'not': r'Rhone|Rhône'},
    'p5-gachet-2':           {'not': r'first'},
    'p3-vangogh-chair':      {'must': r'Chair|stoel', 'not': r'Gauguin'},
    'p3-gauguin-chair':      {'must': r'Gauguin', 'not': r"Van Gogh's Chair|stoel van Van Gogh"},
    'p4-sower-millet':       {'must': r'Millet'},
    'p3-sower':              {'not': r'Millet|naar Millet'},
    'p2-skull':              {'must': r'Skull|skelet'},
    'p4-almond-blossom':     {'must': r'Almond|Amandel'},
}

# 静态版本存疑说明（无论命中与否都在报告中提示复核）
QUESTIONABLE_NOTES = {
    'p3-sunflowers-london': '向日葵三版之一：需核对为伦敦国家美术馆版（1888年8月，15朵）',
    'p3-sunflowers-munich': '向日葵三版之一：需核对为慕尼黑新绘画陈列馆版（12朵，浅蓝背景）',
    'p3-sunflowers-amsterdam': '向日葵三版之一：需核对为阿姆斯特丹梵高博物馆 1889 年 1 月重绘版',
    'p3-bedroom': '阿尔勒的卧室共三版：需核对为阿姆斯特丹梵高博物馆 1888 年 10 月第一版',
    'p2-self-aic': '自画像多幅：需核对为芝加哥艺术博物馆 1887 年版',
    'p2-self-orsay-87': '自画像多幅：需核对为奥赛博物馆 1887 年版',
    'p4-self-orsay-89': '自画像多幅：需核对为奥赛博物馆 1889 年 9 月版（蓝绿色涡旋背景）',
    'p4-self-nga-89': '自画像多幅：需核对为华盛顿美国国家美术馆 1889 年 8 月版',
    'p5-gachet-1': '加歇医生两版：需核对为第一版（私人收藏，桌上两本书+毛地黄）',
    'p5-gachet-2': '加歇医生两版：需核对为第二版（奥赛，无书）',
    'p3-ginoux': '吉努夫人两版：需核对为奥赛 1888 年 11 月版（桌上书本与阳伞）',
    'p4-ginoux-rome': '吉努夫人两版：需核对为罗马国立现代艺术美术馆 1890 年版（仿高更速写）',
    'p3-sower': '播种者多版：需核对为 1888 年 11 月阿尔勒版（落日与麦田）',
    'p4-sower-millet': '播种者多版：需核对为仿米勒的播种者（1890，圣雷米）',
    'p2-tanguy': '唐吉老爹肖像多版：需核对为巴黎罗丹博物馆版（日本版画背景）',
}

# ----------------------------------------------------------------------------
# Met（纽约大都会博物馆）目标：id -> (objectID 或 None, 标题匹配正则, 搜索词)
# ----------------------------------------------------------------------------
MET_TARGETS = {
    'p2-self-straw-hat': (436532, r'straw hat', 'Self-Portrait with a Straw Hat Van Gogh'),
    'p3-oleanders':      (None,   r'^oleanders$', 'Oleanders Van Gogh'),
    'p4-wf-cypresses':   (None,   r'^wheat field with cypresses', 'Wheat Field with Cypresses Van Gogh'),
    'p4-cypresses':      (None,   r'^cypresses$', 'Cypresses Van Gogh'),
    'p4-olive-trees':    (None,   r'^olive trees', 'Olive Trees Van Gogh'),
    'p4-first-steps':    (None,   r'^first steps', 'First Steps after Millet Van Gogh'),
}
MET_API = 'https://collectionapi.metmuseum.org/public/collection/v1'
MET_SEARCH = 'https://collectionapi.metmuseum.org/public/collection/v1.1/search'

# ----------------------------------------------------------------------------
# HTTP 基础
# ----------------------------------------------------------------------------
_print_lock = threading.Lock()
_neg_lock = threading.Lock()
NEG_CACHE = {}
STATS = {'requests': 0, 'retries': 0}


def log(msg):
    with _print_lock:
        print(msg, flush=True)


def load_neg_cache():
    global NEG_CACHE
    try:
        with open(NEG_CACHE_PATH, 'r', encoding='utf-8') as f:
            NEG_CACHE = json.load(f)
    except Exception:
        NEG_CACHE = {}


def save_neg_cache():
    try:
        with open(NEG_CACHE_PATH, 'w', encoding='utf-8') as f:
            json.dump(NEG_CACHE, f, ensure_ascii=False)
    except Exception:
        pass


def fetch(url, opener=PROXY_OPENER, timeout=30, method='GET', headers=None):
    """返回 (status, content_type, bytes)；HTTP 4xx/5xx 不抛异常。网络错误抛异常。"""
    h = dict(UA)
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h, method=method)
    with opener.open(req, timeout=timeout) as resp:
        return resp.status, resp.headers.get('Content-Type', ''), resp.read()


def fetch_no_raise(url, **kw):
    try:
        return fetch(url, **kw)
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.headers.get('Content-Type', ''), b''
        except Exception:
            return e.code, '', b''
    except Exception as e:
        raise


def wsrv_url(filename, width=VERIFY_WIDTH):
    inner = COMMONS_PATH + filename.replace(' ', '_') + '?width=%d' % width
    return WSRV + urllib.parse.quote(inner, safe='')


def image_dims(data):
    """从 JPEG/PNG/WebP 字节解析 (w, h)；失败返回 None。"""
    if not data or len(data) < 32:
        return None
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')
    if data[:3] == b'RIFF' and data[8:12] == b'WEBP':
        # VP8X / VP8 / VP8L 三种头
        if data[12:16] == b'VP8X':
            w = int.from_bytes(data[24:27], 'little') + 1
            h = int.from_bytes(data[27:30], 'little') + 1
            return w, h
        if data[12:16] == b'VP8 ':
            w = int.from_bytes(data[26:28], 'little') & 0x3fff
            h = int.from_bytes(data[28:30], 'little') & 0x3fff
            return w, h
        if data[12:16] == b'VP8L':
            b = data[21:25]
            w = 1 + (((b[1] & 0x3f) << 8) | b[0])
            h = 1 + (((b[3] & 0xf) << 10) | (b[2] << 2) | ((b[1] & 0xc0) >> 6))
            return w, h
        return None
    if data[:2] == b'\xff\xd8':  # JPEG
        i = 2
        n = len(data)
        while i + 9 < n:
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                i += 2
                continue
            if marker == 0xDA:  # SOS，之后是压缩数据，不再有 SOF
                return None
            seg_len = int.from_bytes(data[i + 2:i + 4], 'big')
            if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                          0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                h = int.from_bytes(data[i + 5:i + 7], 'big')
                w = int.from_bytes(data[i + 7:i + 9], 'big')
                return w, h
            i += 2 + seg_len
        return None
    return None


def dims_via_wsrv_json(filename):
    """兜底：用 wsrv 的 output=json 读取宽高。"""
    try:
        status, ct, body = fetch_no_raise(wsrv_url(filename) + '&output=json')
        if status == 200:
            j = json.loads(body.decode('utf-8', 'replace'))
            if j.get('width') and j.get('height'):
                return int(j['width']), int(j['height'])
    except Exception:
        pass
    return None


def check_commons(filename):
    """
    验证一个 Commons 候选文件名。
    返回 (ok, w, h)。ok=True 仅当 wsrv 返回 HTTP 200 且 Content-Type 以 image/ 开头。
    404 记入负缓存（确定性结果，不进入任何输出）。
    """
    with _neg_lock:
        if filename in NEG_CACHE:
            return False, None, None
    url = wsrv_url(filename)
    last_err = None
    for attempt in range(2):
        try:
            STATS['requests'] += 1
            status, ct, data = fetch_no_raise(url)
            if status == 200 and ct.lower().startswith('image/'):
                wh = image_dims(data) or dims_via_wsrv_json(filename)
                if wh:
                    return True, wh[0], wh[1]
                return True, None, None  # 已验证为图像，只是无法解析尺寸（极少见）
            if status == 404:
                with _neg_lock:
                    NEG_CACHE[filename] = True
                return False, None, None
            if status is not None and status < 500 and status != 429:
                return False, None, None  # 403 等确定性拒绝，不重试
            last_err = 'HTTP %s' % status
        except Exception as e:
            STATS['retries'] += 1
            last_err = repr(e)[:120]
        time.sleep(0.8 + random.random())
    if last_err:
        log('    ! %s -> %s' % (filename[:70], last_err))
    return False, None, None


# ----------------------------------------------------------------------------
# Commons 候选生成
# ----------------------------------------------------------------------------
PATTERN_AUTHORS = ['Van Gogh', 'Vincent van Gogh']
TEMPLATES = [
    '{a} - {t} - Google Art Project.jpg',
    '{t} - {a}.jpg',
    '{t} by {a}.jpg',
    '{a} - {t}.jpg',
    '{t}.jpg',
    '{t}, {y}, {a}.jpg',
    '{a} - {t} (Yorck).jpg',
    '{a} - {t} - Google Art Project (extra image).jpg',
]


def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s)
                   if not unicodedata.combining(c))


def title_variants(title):
    """生成标题的各种命名变体。"""
    out = []

    def add(x):
        x = x.strip()
        if x and x not in out:
            out.append(x)

    add(title)
    de = strip_accents(title)
    add(de)
    # 冠词增删
    if title.startswith('The '):
        add(title[4:].strip())
    else:
        add('The ' + title)
    # 括注：整串保留 + 去掉括注
    m = re.match(r'^(.*?)\s*\((.*)\)\s*$', title)
    if m:
        add(m.group(1).strip())
        add(strip_accents(m.group(1).strip()))
    # 冒号（Commons 文件名不允许冒号）
    if ':' in title:
        head, tail = title.split(':', 1)
        add((head + ' - ' + tail.strip()).strip())
        add(head.strip())
    # Wheat Field <-> Wheatfield
    for base in list(out):
        if 'Wheat Field' in base:
            add(base.replace('Wheat Field', 'Wheatfield'))
        if 'Wheatfield' in base:
            add(base.replace('Wheatfield', 'Wheat Field'))
    # Self-Portrait 大小写
    for base in list(out):
        if 'Self-Portrait' in base:
            add(base.replace('Self-Portrait', 'Self-portrait'))
        if ' with ' in base:
            add(base.replace(' with ', ' With '))
    return out


def candidates_for(work):
    """一幅作品的全部候选文件名，按优先级排序。"""
    wid = work['id']
    out = []

    def add(x):
        x = (x or '').strip()
        if x and x not in out:
            out.append(x)

    for x in WEBSEARCH_FOUND.get(wid, []):
        add(x)
    for x in work.get('guesses', []):
        add(x)
    for x in KNOWN.get(wid, []):
        add(x)

    year = ''
    m = re.search(r'(\d{4})', work.get('date', ''))
    if m:
        year = m.group(1)
    for t in title_variants(work['en']):
        for a in PATTERN_AUTHORS:
            for tpl in TEMPLATES:
                add(tpl.format(a=a, t=t, y=year))
    return out


def expect_ok(filename, wid):
    """命中文件名是否满足该作品的多版本消歧约束。"""
    exp = EXPECT.get(wid)
    if not exp:
        return True, None
    must = exp.get('must')
    notr = exp.get('not')
    if must and not re.search(must, filename, re.I):
        return False, 'must[%s] 不匹配' % must
    if notr and re.search(notr, filename, re.I):
        return False, '命中了排除规则[%s]' % notr
    return True, None


def resolve_commons(work):
    """逐个验证候选，返回 (entry, note) 或 (None, 原因)。"""
    wid = work['en']
    cands = candidates_for(work)
    fallback = None       # 不满足 expect 的首个命中
    fallback_note = None
    tried = 0
    for cand in cands:
        ok, w, h = check_commons(cand)
        if not ok:
            tried += 1
            continue
        good, why = expect_ok(cand, work['id'])
        if good:
            note = None
            return {'source': 'commons', 'file': cand, 'w': w, 'h': h}, note, tried
        if fallback is None:
            fallback = cand
            fallback_note = why
            fallback_wh = (w, h)
    if fallback:
        w, h = fallback_wh
        return ({'source': 'commons', 'file': fallback, 'w': w, 'h': h},
                '版本存疑：' + fallback_note, tried)
    return None, '全部 %d 个候选均未命中（404/失败）' % len(cands), tried


# ----------------------------------------------------------------------------
# Met 解析
# ----------------------------------------------------------------------------
def met_fetch_object(object_id):
    status, ct, body = fetch_no_raise('%s/objects/%d' % (MET_API, object_id))
    if status != 200:
        return None
    try:
        return json.loads(body.decode('utf-8'))
    except Exception:
        return None


def met_search_ids(query):
    """v1.1 搜索，返回 objectID 列表（可能为空）。"""
    for url in (
        MET_SEARCH + '?' + urllib.parse.urlencode({'q': query, 'hasImages': 'true'}),
        MET_SEARCH + '?' + urllib.parse.urlencode({'q': query}),
    ):
        try:
            status, ct, body = fetch_no_raise(url)
            if status == 200:
                j = json.loads(body.decode('utf-8'))
                ids = j.get('objectIDs') or []
                if ids:
                    return ids
        except Exception:
            continue
    return []


def met_is_van_gogh(obj):
    name = (obj.get('artistDisplayName') or '')
    if 'van gogh' not in name.lower():
        return False
    return True


def resolve_met(work, title_re, search_q, known_id=None):
    """
    解析一幅 Met 作品。返回 (entry, note) 或 (None, 原因)。
    entry['small'] / entry['original'] 均经过实际网络验证。
    """
    # 候选 objectID：已知 ID 优先，然后搜索结果
    ids = []
    if known_id:
        ids.append(known_id)
    for oid in met_search_ids(search_q)[:40]:
        if oid not in ids:
            ids.append(oid)
    # 兜底：扫描梵高作品聚集的 objectID 区段
    if not ids:
        ids = list(range(436500, 436561))

    pat = re.compile(title_re, re.I)
    checked = 0
    for oid in ids:
        if checked >= 40 and oid != known_id:
            break
        obj = met_fetch_object(oid)
        if not obj or not met_is_van_gogh(obj):
            continue
        checked += 1
        title = obj.get('title') or ''
        if not pat.search(title):
            continue
        small = obj.get('primaryImageSmall') or ''
        original = obj.get('primaryImageSmall') and obj.get('primaryImage') or obj.get('primaryImage') or ''
        if not small:
            small = original
        if not small:
            continue
        # 验证 small（完整下载，解析尺寸）
        ok, w, h = False, None, None
        for opener in (PROXY_OPENER, DIRECT_OPENER):
            try:
                STATS['requests'] += 1
                status, ct, data = fetch(small, opener=opener)
                if status == 200 and ct.lower().startswith('image/'):
                    ok = True
                    wh = image_dims(data) or dims_via_wsrv_json(small)
                    w, h = (wh if wh else (None, None))
                    break
            except Exception:
                continue
        if not ok:
            continue
        # 验证 original（轻量：Range 请求，接受 200/206 + image/*）
        orig_ok = False
        if original and original != small:
            for opener in (PROXY_OPENER, DIRECT_OPENER):
                try:
                    STATS['requests'] += 1
                    status, ct, _ = fetch(
                        original, opener=opener,
                        headers={'Range': 'bytes=0-1023'})
                    if status in (200, 206) and ct.lower().startswith('image/'):
                        orig_ok = True
                        break
                except Exception:
                    continue
        else:
            orig_ok = True
        entry = {
            'source': 'met',
            'small': small,
            'original': (original if orig_ok else small),
            'objectID': obj.get('objectID'),
            'w': w, 'h': h,
        }
        note = None if orig_ok else 'original 图校验失败，zoom 暂用 web-large'
        return entry, note
    return None, 'Met 搜索/扫描未找到匹配对象（title 正则 %s）' % title_re


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def resolve_work(work):
    """返回 (entry|None, source_note, fail_reason)。"""
    wid = work['id']
    # Met 作品优先官方图源；失败则回落 Commons
    if wid in MET_TARGETS:
        known_id, title_re, search_q = MET_TARGETS[wid]
        try:
            entry, note = resolve_met(work, title_re, search_q, known_id)
        except Exception as e:
            entry, note = None, 'Met 异常: %r' % e
        if entry:
            return entry, note or 'Met 官方图源（objectID %s）' % entry.get('objectID'), None
        log('    Met 未命中，回落 Commons：%s' % wid)
    try:
        entry, note, tried = resolve_commons(work)
    except Exception as e:
        return None, None, '解析异常: %r' % e
    if entry:
        return entry, note, None
    return None, None, note


def main():
    only = None
    met_only = False
    args = sys.argv[1:]
    if '--only' in args:
        i = args.index('--only')
        only = [x.strip() for x in args[i + 1].split(',') if x.strip()]
    if '--met-only' in args:
        met_only = True

    with open(MANIFEST_PATH, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
    works = manifest['works']
    load_neg_cache()
    log('载入 manifest：%d 幅作品；负缓存 %d 条；代理 %s' % (
        len(works), len(NEG_CACHE), PROXY))

    targets = works
    if only:
        targets = [w for w in works if w['id'] in only]
    elif met_only:
        targets = [w for w in works if w['id'] in MET_TARGETS]

    results = {}   # id -> (entry, note, fail_reason)
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        futs = {pool.submit(resolve_work, w): w for w in targets}
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            w = futs[fut]
            done += 1
            try:
                entry, note, fail = fut.result()
            except Exception as e:
                entry, note, fail = None, None, '线程异常: %r' % e
            results[w['id']] = (entry, note, fail)
            if entry:
                tag = entry['file'] if entry['source'] == 'commons' else (
                    'Met #%s' % entry.get('objectID'))
                log('[%3d/%3d] ✓ %-24s %s' % (done, len(targets), w['id'], tag[:90]))
            else:
                log('[%3d/%3d] ✗ %-24s %s' % (done, len(targets), w['id'], (fail or '')[:90]))

    save_neg_cache()
    log('完成：%d 幅 / 耗时 %.1fs / 请求数 %d（重试 %d）' % (
        len(results), time.time() - t0, STATS['requests'], STATS['retries']))

    # ---- 汇总与查重 ----
    resolved = {wid: v for wid, v in results.items() if v[0]}
    failed = {wid: v for wid, v in results.items() if not v[0]}

    file_owners = {}
    for wid, (entry, _, _) in resolved.items():
        if entry and entry.get('source') == 'commons':
            file_owners.setdefault(entry['file'], []).append(wid)
    dup_files = {f: ids for f, ids in file_owners.items() if len(ids) > 1}

    questionable = set(QUESTIONABLE_NOTES.keys())
    # expect 违背 / 撞名 → 版本存疑
    for wid, (entry, note, _) in resolved.items():
        if note and '版本存疑' in note:
            questionable.add(wid)
        if entry and entry.get('source') == 'commons':
            if entry['file'] in dup_files:
                questionable.add(wid)

    # ---- 写 images.json（按 manifest 顺序，仅验证成功条目）----
    if not only and not met_only:
        out = {}
        for w in works:
            r = results.get(w['id'])
            if r and r[0]:
                e = r[0]
                item = {'source': e['source']}
                if e['source'] == 'commons':
                    item['file'] = e['file']
                else:
                    item['small'] = e['small']
                    item['original'] = e['original']
                    item['objectID'] = e['objectID']
                item['w'] = e.get('w')
                item['h'] = e.get('h')
                out[w['id']] = item
        with open(IMAGES_OUT, 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
            f.write('\n')
        log('已写出 %s（%d 条）' % (IMAGES_OUT, len(out)))

        # ---- 写报告 ----
        lines = []
        ap = lines.append
        ap('梵高线上博物馆 · 图像来源解析报告')
        ap('=' * 60)
        ap('生成时间：%s' % time.strftime('%Y-%m-%d %H:%M:%S'))
        ap('总计 %d 幅作品：已解决 %d，未解决 %d（目标 ≥85%%）' % (
            len(results), len(resolved), len(failed)))
        ap('')
        ap('【Met 官方图源】')
        met_used = [(w['id'], r) for w in works for wid, r in [(w['id'], results.get(w['id']))] if r and r[0] and r[0]['source'] == 'met']
        for wid, (entry, note, _) in met_used:
            ap('  %s  objectID %s  %sx%s  %s' % (
                wid, entry.get('objectID'), entry.get('w'), entry.get('h'),
                (note or '')[:60]))
        if not met_used:
            ap('  （无）')
        ap('')
        ap('【已解决 · Commons】')
        for w in works:
            r = results.get(w['id'])
            if r and r[0] and r[0]['source'] == 'commons':
                e = r[0]
                mark = '（版本存疑）' if w['id'] in questionable else ''
                ap('  %-24s %s  [%sx%s] %s' % (
                    w['id'], mark, e.get('w'), e.get('h'), e['file']))
        ap('')
        ap('【版本存疑清单】')
        for w in works:
            if w['id'] in questionable:
                r = results.get(w['id'])
                detail = QUESTIONABLE_NOTES.get(w['id'], '')
                if r and r[0]:
                    if r[0]['source'] == 'commons':
                        detail += ' | 实际命中：%s' % r[0]['file']
                    if r[1] and '版本存疑' in r[1]:
                        detail += ' | %s' % r[1]
                ap('  %s：%s' % (w['id'], detail))
        if dup_files:
            ap('  —— 以下文件被多幅作品共用（需人工消歧）——')
            for f, ids in dup_files.items():
                ap('  %s <- %s' % (f, ', '.join(ids)))
        ap('')
        ap('【未解决】')
        for w in works:
            r = results.get(w['id'])
            if r and not r[0]:
                ap('  %-24s %s' % (w['id'], (r[2] or '未知原因')))
        ap('')
        with open(REPORT_OUT, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        log('已写出 %s' % REPORT_OUT)

    # 摘要
    log('-' * 60)
    log('成功 %d / %d（%.1f%%）' % (
        len(resolved), len(results), 100.0 * len(resolved) / max(1, len(results))))
    must_ids = [w['id'] for w in works if w.get('must')]
    must_ok = [i for i in must_ids if results.get(i) and results[i][0]]
    log('must 作品：%d/%d 已解决%s' % (
        len(must_ok), len(must_ids),
        ('，缺：' + ', '.join(i for i in must_ids if i not in must_ok)) if len(must_ok) != len(must_ids) else ''))


if __name__ == '__main__':
    main()
