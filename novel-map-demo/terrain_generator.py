"""
地形图生成器
真实地理：用matplotlib绘制中国历史地理底图
虚构世界：用Perlin Noise生成程序化地形
"""
import math
import random
import io
import base64
import logging
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

logger = logging.getLogger(__name__)

# ========== 颜色方案 ==========
# 地形高度→颜色映射（类FMG风格）
TERRAIN_COLORS = {
    "deep_ocean": (30, 60, 120),
    "ocean": (45, 85, 150),
    "shallow_water": (70, 130, 180),
    "coast": (194, 178, 128),
    "beach": (210, 200, 150),
    "lowland": (120, 170, 80),
    "grassland": (90, 150, 60),
    "forest": (60, 120, 50),
    "highland": (140, 130, 100),
    "mountain": (130, 120, 110),
    "high_mountain": (180, 175, 170),
    "snow": (240, 240, 245),
}

# ★ 生物群落颜色（根据区域/地点描述着色，而非纯高度）
BIOME_COLORS = {
    "desert": (210, 185, 110),     # 沙漠：金黄色
    "sand": (210, 190, 120),        # 沙地
    "snow": (235, 240, 248),        # 雪地/冰原
    "tundra": (195, 210, 220),      # 苔原
    "grassland": (140, 185, 90),    # 草原
    "plain": (150, 175, 95),        # 平原
    "swamp": (70, 95, 55),          # 沼泽
    "volcanic": (80, 50, 45),       # 火山
}

# ★ 区域名→生物群落映射
REGION_BIOME_KEYWORDS = {
    "漠": "desert",    # 西漠、大漠 → 沙漠
    "沙": "desert",    # 沙地、沙海 → 沙漠
    "海": "ocean",     # 东海、南海 → 海洋
    "湖": "ocean",     # xx湖 → 湖泊
    "原": "grassland", # 北原、草原 → 草原
    "州": "plain",     # 中州、九州 → 平原
    "疆": "hills",     # 南疆 → 丘陵
    "林": "forest",    # xx林 → 森林
    "雪": "snow",      # 雪山、雪域 → 雪地
    "冰": "tundra",    # 冰原、冰域 → 苔原
    "沼": "swamp",     # 沼泽 → 沼泽
    "火": "volcanic",  # 火山 → 火山
}

def detect_region_biome(region_name):
    """从区域名中检测生物群落类型"""
    for keyword, biome in REGION_BIOME_KEYWORDS.items():
        if keyword in region_name:
            return biome
    return None

# 区域填色（半透明叠加）
REGION_OVERLAY_COLORS = [
    (52, 152, 219, 40),    # 蓝
    (46, 204, 113, 40),    # 绿
    (231, 76, 60, 40),     # 红
    (243, 156, 18, 40),    # 橙
    (155, 89, 182, 40),    # 紫
    (26, 188, 156, 40),    # 青
    (230, 126, 34, 40),    # 深橙
    (52, 73, 94, 40),      # 深灰蓝
]

REGION_BORDER_COLORS = [
    (52, 152, 219, 180),
    (46, 204, 113, 180),
    (231, 76, 60, 180),
    (243, 156, 18, 180),
    (155, 89, 182, 180),
    (26, 188, 156, 180),
    (230, 126, 34, 180),
    (52, 73, 94, 180),
]


def _height_to_color(h):
    """将高度值(0-100)映射为颜色"""
    if h < 15:
        return TERRAIN_COLORS["deep_ocean"]
    elif h < 25:
        return TERRAIN_COLORS["ocean"]
    elif h < 32:
        return TERRAIN_COLORS["shallow_water"]
    elif h < 36:
        return TERRAIN_COLORS["coast"]
    elif h < 40:
        return TERRAIN_COLORS["beach"]
    elif h < 50:
        return TERRAIN_COLORS["lowland"]
    elif h < 58:
        return TERRAIN_COLORS["grassland"]
    elif h < 65:
        return TERRAIN_COLORS["forest"]
    elif h < 72:
        return TERRAIN_COLORS["highland"]
    elif h < 80:
        return TERRAIN_COLORS["mountain"]
    elif h < 88:
        return TERRAIN_COLORS["high_mountain"]
    else:
        return TERRAIN_COLORS["snow"]


def _lerp_color(c1, c2, t):
    """颜色插值"""
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))


def generate_real_terrain(locations, region_groups=None, map_bounds=None, size=1200):
    """
    生成真实地理风格的地形底图
    locations: [{"name": str, ...}]
    region_groups: {"区域名": ["地点1", "地点2"]}
    map_bounds: (x1, y1, x2, y2) 地图范围
    size: 图片像素大小
    返回: base64编码的PNG图片
    """
    from geo_database import lookup_batch

    known = lookup_batch(locations)
    if not known:
        # 没有已知坐标，回退到虚构世界
        return generate_fictional_terrain(locations, region_groups, size)

    # 计算地图范围（基于已知坐标，加边距）
    xs = [v["x"] for v in known.values()]
    ys = [v["y"] for v in known.values()]
    if map_bounds:
        x1, y1, x2, y2 = map_bounds
    else:
        margin = 120
        x1 = max(0, min(xs) - margin)
        y1 = max(0, min(ys) - margin)
        x2 = min(1000, max(xs) + margin)
        y2 = min(1000, max(ys) + margin)

    # 创建高度图
    w, h = size, size
    heightmap = np.zeros((h, w), dtype=np.float64)

    # 基础地形：用多层Perlin Noise生成自然地形
    # 坐标映射：地图坐标→图片坐标
    def map_to_img(mx, my):
        ix = int((mx - x1) / (x2 - x1) * w)
        iy = int((y2 - my) / (y2 - y1) * h)  # y轴翻转（北在上）
        return max(0, min(w-1, ix)), max(0, min(h-1, iy))

    # 生成多层Perlin Noise
    perlin = PerlinNoise(seed=42)
    for py in range(0, h):
        for px in range(0, w):
            # 图片坐标→地图坐标
            mx = x1 + px / w * (x2 - x1)
            my = y2 - py / h * (y2 - y1)
            # 多层叠加
            n = perlin.octave_noise(mx * 0.005, my * 0.005, octaves=6, persistence=0.5)
            heightmap[py, px] = (n + 1) * 25  # 映射到0-50范围

    # 平滑
    from scipy.ndimage import gaussian_filter
    heightmap = gaussian_filter(heightmap, sigma=3)

    # 根据已知地点调整地形
    for name, info in known.items():
        ix, iy = map_to_img(info["x"], info["y"])
        loc_type = info.get("type", "other")

        # 在地点周围创建地形特征
        radius = 30
        for dy in range(-radius, radius+1, 2):
            for dx in range(-radius, radius+1, 2):
                px, py = ix + dx, iy + dy
                if 0 <= px < w and 0 <= py < h:
                    dist = math.sqrt(dx*dx + dy*dy)
                    if dist > radius:
                        continue
                    factor = 1 - dist / radius

                    if loc_type == "mountain":
                        heightmap[py, px] += factor * 35
                    elif loc_type == "river":
                        heightmap[py, px] -= factor * 20
                    elif loc_type == "sea":
                        heightmap[py, px] -= factor * 25
                    elif loc_type == "forest":
                        heightmap[py, px] += factor * 10
                    elif loc_type == "city":
                        heightmap[py, px] += factor * 5
                    elif loc_type == "desert":
                        heightmap[py, px] += factor * 8
                    elif loc_type == "building":
                        heightmap[py, px] += factor * 3

    # 重新平滑
    heightmap = gaussian_filter(heightmap, sigma=2)

    # 渲染为图片
    img = _render_heightmap(heightmap, region_groups, known, x1, y1, x2, y2, w, h)

    return img


def generate_fictional_terrain(locations=None, region_groups=None, region_traits=None, region_zones=None, size=1200):
    """
    生成虚构世界的程序化地形底图（v2：地点驱动 + 生物群落着色）
    """
    from scipy.ndimage import gaussian_filter

    w, h = size, size
    seed = random.randint(0, 99999)
    random.seed(seed)

    # ===== 第1步：生成基础地图（默认75%陆地，25%海洋） =====
    continent_perlin = PerlinNoise(seed=seed)
    detail_perlin = PerlinNoise(seed=seed + 50)
    micro_perlin = PerlinNoise(seed=seed + 200)

    noise_values = np.zeros((h // 2 + 1, w // 2 + 1), dtype=np.float64)
    for py in range(0, h, 2):
        for px in range(0, w, 2):
            n1 = continent_perlin.octave_noise(px * 0.002, py * 0.002, octaves=5, persistence=0.5)
            n2 = detail_perlin.octave_noise(px * 0.006, py * 0.006, octaves=3, persistence=0.6)
            n3 = micro_perlin.noise(px * 0.015, py * 0.015)
            noise_values[py // 2, px // 2] = n1 * 0.6 + n2 * 0.3 + n3 * 0.1

    flat_noise = noise_values.flatten()
    sea_threshold = np.percentile(flat_noise, 25)

    base_land = 50
    sea_depth = 15
    heightmap = np.zeros((h, w), dtype=np.float64)
    for py in range(0, h, 2):
        for px in range(0, w, 2):
            nv = noise_values[py // 2, px // 2]
            if nv < sea_threshold:
                factor = (sea_threshold - nv) / (sea_threshold - flat_noise.min() + 0.01)
                heightmap[py, px] = sea_depth * (1 - factor * 0.5)
            else:
                factor = (nv - sea_threshold) / (flat_noise.max() - sea_threshold + 0.01)
                heightmap[py, px] = base_land + factor * 20

    heightmap = gaussian_filter(heightmap, sigma=3)

    # ===== 第2步：地点驱动地形雕刻 =====
    if locations:
        merged_zones = _merge_nearby_locations(locations, w, h)
        for zone in merged_zones:
            loc_type = zone["type"]
            points = zone["points"]
            avg_x = sum(p[0] for p in points) / len(points)
            avg_y = sum(p[1] for p in points) / len(points)
            spread = max(math.sqrt(sum((p[0]-avg_x)**2 + (p[1]-avg_y)**2 for p in points) / len(points)), 20)
            radius = int(spread * 1.5 + 30)

            if loc_type == "mountain":
                _carve_mountain_v2(heightmap, avg_x, avg_y, radius, peak=80, perlin_seed=seed)
            elif loc_type in ("city", "building"):
                _carve_flatland_v2(heightmap, avg_x, avg_y, radius, height=45)
            elif loc_type == "forest":
                _carve_forest_v2(heightmap, avg_x, avg_y, radius, height=50)
            elif loc_type in ("river", "sea"):
                _carve_water_v2(heightmap, avg_x, avg_y, radius, depth=12)
            elif loc_type == "secret_realm":
                _carve_secret_realm_v2(heightmap, avg_x, avg_y, radius, seed=seed)
            elif loc_type == "desert":
                _carve_flatland_v2(heightmap, avg_x, avg_y, radius, height=42)
            else:
                _carve_flatland_v2(heightmap, avg_x, avg_y, radius=max(20, radius-10), height=40)

    # ===== 第3步：最终平滑 =====
    heightmap = gaussian_filter(heightmap, sigma=4)
    heightmap = gaussian_filter(heightmap, sigma=1.5)

    # ===== 第4步：校准海陆比例 =====
    SEA_LEVEL = 32
    flat_hm = heightmap.flatten()
    current_land_ratio = np.sum(flat_hm >= SEA_LEVEL) / len(flat_hm)
    target_land_ratio = 0.75
    if current_land_ratio < target_land_ratio:
        low_shift, high_shift = 0, 50
        for _ in range(15):
            mid_shift = (low_shift + high_shift) / 2
            test_ratio = np.sum((flat_hm + mid_shift) >= SEA_LEVEL) / len(flat_hm)
            if test_ratio < target_land_ratio:
                low_shift = mid_shift
            else:
                high_shift = mid_shift
        heightmap += (low_shift + high_shift) / 2
    elif current_land_ratio > target_land_ratio + 0.1:
        low_shift, high_shift = -50, 0
        for _ in range(15):
            mid_shift = (low_shift + high_shift) / 2
            test_ratio = np.sum((flat_hm + mid_shift) >= SEA_LEVEL) / len(flat_hm)
            if test_ratio > target_land_ratio:
                low_shift = mid_shift
            else:
                high_shift = mid_shift
        heightmap += (low_shift + high_shift) / 2

    # ===== 第5步：渲染 =====
    known_coords = {}
    if locations:
        for loc in locations:
            name = loc.get("name", "")
            if name:
                known_coords[name] = {"x": loc.get("x", 500), "y": loc.get("y", 500)}

    img = _render_heightmap(heightmap, region_groups, known_coords, 0, 0, 1000, 1000, w, h, region_traits=region_traits)
    return img


# ========== P1: 合并相邻同类地点 ==========

def _merge_nearby_locations(locations, w, h):
    """将距离较近的同类地点合并为一个地形区域"""
    # 转换为图片坐标
    loc_points = []
    for loc in locations:
        lx = loc.get("x", 500) / 1000 * w
        ly = (1000 - loc.get("y", 500)) / 1000 * h
        loc_points.append({
            "name": loc.get("name", ""),
            "type": loc.get("type", "other"),
            "ix": lx, "iy": ly,
        })

    # 简单聚类：同类且距离<150px的合并
    merge_threshold = 150
    visited = [False] * len(loc_points)
    zones = []

    for i in range(len(loc_points)):
        if visited[i]:
            continue
        zone = {"type": loc_points[i]["type"], "points": [(loc_points[i]["ix"], loc_points[i]["iy"])]}
        visited[i] = True
        for j in range(i + 1, len(loc_points)):
            if visited[j]:
                continue
            if loc_points[j]["type"] != zone["type"]:
                continue
            # 检查是否和zone中任意一点距离足够近
            for px, py in zone["points"]:
                d = math.sqrt((loc_points[j]["ix"] - px)**2 + (loc_points[j]["iy"] - py)**2)
                if d < merge_threshold:
                    zone["points"].append((loc_points[j]["ix"], loc_points[j]["iy"]))
                    visited[j] = True
                    break
        zones.append(zone)

    return zones


# ========== v2 地形雕刻函数（加权混合，无硬边界） ==========

def _carve_mountain_v2(heightmap, cx, cy, radius=50, peak=80, perlin_seed=0):
    """v2: 不规则山脉，加权混合"""
    h, w = heightmap.shape
    perlin = PerlinNoise(seed=perlin_seed + int(cx * 7 + cy * 13))
    for dy in range(-radius, radius + 1, 2):
        for dx in range(-radius, radius + 1, 2):
            px, py = int(cx + dx), int(cy + dy)
            if not (0 <= px < w and 0 <= py < h):
                continue
            dist = math.sqrt(dx * dx + dy * dy)
            if dist > radius:
                continue
            # 基础衰减：高斯
            base_factor = math.exp(-(dist * dist) / (2 * (radius * 0.45) ** 2))
            # 噪声扰动：让山脉形状不规则
            noise = perlin.noise(px * 0.02, py * 0.02) * 0.3
            factor = max(0, min(1, base_factor + noise))
            # 目标高度：中心高，边缘低
            target = peak * factor + 35 * (1 - factor)
            # ★ 加权混合（非max）
            blend = factor * 0.85
            heightmap[py, px] = heightmap[py, px] * (1 - blend) + target * blend


def _carve_flatland_v2(heightmap, cx, cy, radius=40, height=45):
    """v2: 平坦陆地，柔和过渡"""
    h, w = heightmap.shape
    for dy in range(-radius, radius + 1, 2):
        for dx in range(-radius, radius + 1, 2):
            px, py = int(cx + dx), int(cy + dy)
            if not (0 <= px < w and 0 <= py < h):
                continue
            dist = math.sqrt(dx * dx + dy * dy)
            if dist > radius:
                continue
            factor = (1 - (dist / radius) ** 2) ** 1.5
            target = height * factor + heightmap[py, px] * (1 - factor * 0.6)
            # 确保不低于海平面
            target = max(target, 33)
            blend = factor * 0.7
            heightmap[py, px] = heightmap[py, px] * (1 - blend) + target * blend


def _carve_forest_v2(heightmap, cx, cy, radius=45, height=50):
    """v2: 森林，中等海拔"""
    h, w = heightmap.shape
    for dy in range(-radius, radius + 1, 2):
        for dx in range(-radius, radius + 1, 2):
            px, py = int(cx + dx), int(cy + dy)
            if not (0 <= px < w and 0 <= py < h):
                continue
            dist = math.sqrt(dx * dx + dy * dy)
            if dist > radius:
                continue
            factor = (1 - (dist / radius) ** 1.8) ** 1.2
            target = height * factor + heightmap[py, px] * (1 - factor * 0.5)
            target = max(target, 32)
            blend = factor * 0.65
            heightmap[py, px] = heightmap[py, px] * (1 - blend) + target * blend


def _carve_water_v2(heightmap, cx, cy, radius=50, depth=12):
    """v2: 水域，柔和下沉"""
    h, w = heightmap.shape
    for dy in range(-radius, radius + 1, 2):
        for dx in range(-radius, radius + 1, 2):
            px, py = int(cx + dx), int(cy + dy)
            if not (0 <= px < w and 0 <= py < h):
                continue
            dist = math.sqrt(dx * dx + dy * dy)
            if dist > radius:
                continue
            factor = (1 - (dist / radius) ** 2) ** 1.3
            target = depth * factor + heightmap[py, px] * (1 - factor * 0.85)
            target = min(target, 28)
            blend = factor * 0.8
            heightmap[py, px] = heightmap[py, px] * (1 - blend) + target * blend


def _carve_secret_realm_v2(heightmap, cx, cy, radius=35, seed=0):
    """v2: 秘境地形"""
    random.seed(seed + int(cx * 100 + cy))
    style = random.choice(["floating_island", "hidden_valley", "crystal_peak"])

    if style == "floating_island":
        _carve_water_v2(heightmap, cx, cy, radius + 15, depth=10)
        _carve_mountain_v2(heightmap, cx, cy, radius // 2 + 10, peak=70, perlin_seed=seed)
    elif style == "hidden_valley":
        for angle_deg in range(0, 360, 20):
            angle = math.radians(angle_deg)
            mx = cx + math.cos(angle) * radius * 0.65
            my = cy + math.sin(angle) * radius * 0.65
            _carve_mountain_v2(heightmap, mx, my, radius // 3, peak=62, perlin_seed=seed + angle_deg)
        _carve_flatland_v2(heightmap, cx, cy, radius // 2, height=38)
    else:
        _carve_mountain_v2(heightmap, cx, cy, radius, peak=88, perlin_seed=seed)


def _render_heightmap(heightmap, region_groups, known_coords, x1, y1, x2, y2, w, h, region_traits=None):
    """将高度图渲染为PNG图片（base64）"""
    # 归一化高度到0-100
    hmin, hmax = heightmap.min(), heightmap.max()
    if hmax - hmin < 0.1:
        hmax = hmin + 1
    normalized = (heightmap - hmin) / (hmax - hmin) * 100

    # 创建RGB图像
    img_array = np.zeros((h, w, 3), dtype=np.uint8)
    for py in range(h):
        for px in range(w):
            hv = normalized[py, px]
            img_array[py, px] = _height_to_color(hv)

    # ★ 叠加生物群落着色（根据区域地形特征）
    if region_groups:
        if region_traits is None:
            region_traits = {}
        for region_name, members in region_groups.items():
            # 优先从region_traits获取biome，其次从区域名检测
            biome = None
            traits = region_traits.get(region_name, {})
            if traits and traits.get("biome"):
                biome = traits["biome"]
            if not biome:
                biome = detect_region_biome(region_name)
            if not biome or biome not in BIOME_COLORS:
                continue
            biome_color = BIOME_COLORS[biome]

            # 收集该区域所有成员的图片坐标
            member_pixels = []
            for m in members:
                if m in known_coords:
                    mx, my = known_coords[m]["x"], known_coords[m]["y"]
                    px = int((mx - x1) / (x2 - x1) * w)
                    py = int((y2 - my) / (y2 - y1) * h)
                    member_pixels.append((px, py))

            if len(member_pixels) < 1:
                continue

            # 计算区域中心和半径
            cx = sum(p[0] for p in member_pixels) / len(member_pixels)
            cy = sum(p[1] for p in member_pixels) / len(member_pixels)
            spread = max(math.sqrt(sum((p[0]-cx)**2 + (p[1]-cy)**2 for p in member_pixels) / len(member_pixels)), 20)
            radius = int(spread * 2.5 + 100)

            # 在区域范围内混合生物群落颜色
            for py in range(max(0, int(cy-radius)), min(h, int(cy+radius)+1), 2):
                for px in range(max(0, int(cx-radius)), min(w, int(cx+radius)+1), 2):
                    dist = math.sqrt((px-cx)**2 + (py-cy)**2)
                    if dist > radius:
                        continue
                    # 高斯衰减（更平缓，覆盖更大范围）
                    factor = math.exp(-(dist*dist) / (2 * (radius*0.6)**2))
                    # 陆地着色
                    if normalized[py, px] > 35:
                        blend = factor * 0.75
                        orig = img_array[py, px].astype(np.float64)
                        img_array[py, px] = np.clip(
                            orig * (1 - blend) + np.array(biome_color) * blend, 0, 255
                        ).astype(np.uint8)
                    elif biome in ("ocean",) and normalized[py, px] < 35:
                        blend = factor * 0.5
                        orig = img_array[py, px].astype(np.float64)
                        img_array[py, px] = np.clip(
                            orig * (1 - blend) + np.array(biome_color) * blend, 0, 255
                        ).astype(np.uint8)

    # 转为PIL Image
    img = Image.fromarray(img_array, 'RGB')

    # 叠加区域板块
    if region_groups:
        overlay = Image.new('RGBA', (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)

        for idx, (rname, members) in enumerate(region_groups.items()):
            # 收集成员坐标
            member_pixels = []
            for m in members:
                if m in known_coords:
                    mx, my = known_coords[m]["x"], known_coords[m]["y"]
                    px = int((mx - x1) / (x2 - x1) * w)
                    py = int((y2 - my) / (y2 - y1) * h)
                    member_pixels.append((px, py))

            if len(member_pixels) >= 3:
                # 计算凸包
                hull = _convex_hull_pixels(member_pixels)
                # 外扩
                cx_h = sum(p[0] for p in hull) / len(hull)
                cy_h = sum(p[1] for p in hull) / len(hull)
                expanded = []
                for px, py in hull:
                    dx = px - cx_h
                    dy = py - cy_h
                    dist = math.sqrt(dx*dx + dy*dy) + 0.1
                    expanded.append((int(px + dx/dist*40), int(py + dy/dist*40)))
                expanded.append(expanded[0])

                # 填充区域
                color = REGION_OVERLAY_COLORS[idx % len(REGION_OVERLAY_COLORS)]
                draw.polygon(expanded, fill=color)
                # 边界线
                border_color = REGION_BORDER_COLORS[idx % len(REGION_BORDER_COLORS)]
                draw.line(expanded, fill=border_color, width=2)

            elif len(member_pixels) == 2:
                p1, p2 = member_pixels
                cx_r = (p1[0]+p2[0])//2
                cy_r = (p1[1]+p2[1])//2
                dx = abs(p1[0]-p2[0])//2 + 40
                dy = abs(p1[1]-p2[1])//2 + 40
                rect = [(cx_r-dx, cy_r-dy), (cx_r+dx, cy_r-dy), (cx_r+dx, cy_r+dy), (cx_r-dx, cy_r+dy)]
                color = REGION_OVERLAY_COLORS[idx % len(REGION_OVERLAY_COLORS)]
                draw.polygon(rect, fill=color)
                border_color = REGION_BORDER_COLORS[idx % len(REGION_BORDER_COLORS)]
                draw.line(rect + [rect[0]], fill=border_color, width=2)

        img = img.convert('RGBA')
        img = Image.alpha_composite(img, overlay)
        img = img.convert('RGB')

    # 添加网格线（淡淡的）
    draw = ImageDraw.Draw(img)
    grid_color = (255, 255, 255, 20)
    for i in range(0, w, w//10):
        draw.line([(i, 0), (i, h)], fill=(200, 200, 200, 30), width=1)
    for i in range(0, h, h//10):
        draw.line([(0, i), (w, i)], fill=(200, 200, 200, 30), width=1)

    # 添加指北针
    _draw_compass(img, w - 60, 60, 30)

    # 添加比例尺
    _draw_scale_bar(img, w, h)

    # 转为base64
    buf = io.BytesIO()
    img.save(buf, format='PNG')
    b64 = base64.b64encode(buf.getvalue()).decode('utf-8')
    return b64


def _draw_compass(img, cx, cy, r):
    """绘制指北针"""
    draw = ImageDraw.Draw(img)
    # 外圈
    draw.ellipse([(cx-r, cy-r), (cx+r, cy+r)], outline=(200, 200, 200), width=1)
    # 北箭头
    draw.polygon([(cx, cy-r+5), (cx-6, cy+5), (cx+6, cy+5)], fill=(200, 60, 60))
    # 南箭头
    draw.polygon([(cx, cy+r-5), (cx-6, cy-5), (cx+6, cy-5)], fill=(180, 180, 180))
    # N标记
    try:
        draw.text((cx-4, cy-r-15), "N", fill=(220, 220, 220))
    except:
        pass


def _draw_scale_bar(img, w, h):
    """绘制比例尺"""
    draw = ImageDraw.Draw(img)
    bar_w = 80
    x0 = w - bar_w - 30
    y0 = h - 30
    draw.line([(x0, y0), (x0+bar_w, y0)], fill=(200, 200, 200), width=2)
    draw.line([(x0, y0-5), (x0, y0+5)], fill=(200, 200, 200), width=1)
    draw.line([(x0+bar_w, y0-5), (x0+bar_w, y0+5)], fill=(200, 200, 200), width=1)
    try:
        draw.text((x0+20, y0-18), "100 里", fill=(200, 200, 200))
    except:
        pass


def _convex_hull_pixels(points):
    """2D凸包"""
    pts = sorted(set(points))
    if len(pts) <= 1:
        return pts
    def cross(o, a, b):
        return (a[0]-o[0])*(b[1]-o[1]) - (a[1]-o[1])*(b[0]-o[0])
    lower = []
    for p in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper = []
    for p in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    return lower[:-1] + upper[:-1]


# ========== Perlin Noise 实现 ==========
class PerlinNoise:
    """经典Perlin Noise 2D实现"""
    def __init__(self, seed=0):
        self.seed = seed
        random.seed(seed)
        self.perm = list(range(256))
        random.shuffle(self.perm)
        self.perm = self.perm + self.perm  # 双倍长度避免溢出
        # 8个方向梯度
        self.grads = [(1,1),(-1,1),(1,-1),(-1,-1),(1,0),(-1,0),(0,1),(0,-1)]

    def _fade(self, t):
        return t * t * t * (t * (t * 6 - 15) + 10)

    def _lerp(self, a, b, t):
        return a + t * (b - a)

    def _grad(self, hash_val, x, y):
        g = self.grads[hash_val % 8]
        return g[0] * x + g[1] * y

    def noise(self, x, y):
        xi = int(math.floor(x)) & 255
        yi = int(math.floor(y)) & 255
        xf = x - math.floor(x)
        yf = y - math.floor(y)
        u = self._fade(xf)
        v = self._fade(yf)

        aa = self.perm[self.perm[xi] + yi]
        ab = self.perm[self.perm[xi] + yi + 1]
        ba = self.perm[self.perm[xi + 1] + yi]
        bb = self.perm[self.perm[xi + 1] + yi + 1]

        x1 = self._lerp(self._grad(aa, xf, yf), self._grad(ba, xf - 1, yf), u)
        x2 = self._lerp(self._grad(ab, xf, yf - 1), self._grad(bb, xf - 1, yf - 1), u)
        return self._lerp(x1, x2, v)

    def octave_noise(self, x, y, octaves=6, persistence=0.5):
        total = 0
        frequency = 1
        amplitude = 1
        max_value = 0
        for _ in range(octaves):
            total += self.noise(x * frequency, y * frequency) * amplitude
            max_value += amplitude
            amplitude *= persistence
            frequency *= 2
        return total / max_value


def _simplex_2d(x, y, seed=0):
    """使用Perlin Noise替代简单随机"""
    perlin = PerlinNoise(seed=seed)
    return perlin.noise(x, y)
