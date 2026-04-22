"""
地图生成引擎 v4
真实地理：数据库坐标 + 地形底图
虚构世界：力导向布局 + 程序化地形底图
"""
import math
import random
import logging
from collections import defaultdict
from geo_database import lookup_batch, detect_factions, get_region_colors
from terrain_generator import generate_real_terrain, generate_fictional_terrain

logger = logging.getLogger(__name__)

LOCATION_COLORS = {
    "city": "#e74c3c", "mountain": "#8B4513", "river": "#3498db",
    "building": "#f39c12", "region": "#95a5a6", "forest": "#27ae60",
    "desert": "#f1c40f", "sea": "#2980b9", "secret_realm": "#9b59b6", "other": "#7f8c8d",
}


class MapEngine:
    def __init__(self):
        pass

    def generate_map(self, analysis_result: dict, world_type: str = "fictional") -> dict:
        locations = analysis_result.get("locations", [])
        relations = analysis_result.get("spatial_relations", [])
        characters = analysis_result.get("characters", [])
        events = analysis_result.get("events", [])

        if not locations:
            return self._empty_map()

        if world_type in ("real", "mixed"):
            return self._generate_real_map(locations, relations, characters, events)
        else:
            return self._generate_fictional_map(locations, relations, characters, events)

    def _empty_map(self):
        return {
            "type": "FeatureCollection", "features": [],
            "metadata": {"center": [500, 500], "zoom": 1, "world_type": "fictional",
                         "stats": {"locations": 0, "relations": 0, "characters": 0, "events": 0},
                         "terrain_image": None}
        }

    # ==================== 真实地理 ====================
    def _generate_real_map(self, locations, relations, characters, events):
        # ★ 使用真实经纬度
        known_coords = lookup_batch(locations, use_lnglat=True)
        all_coords = self._assign_unknown_coords_lnglat(locations, known_coords, relations)
        region_groups, region_traits = self._build_region_groups(locations, characters)

        features = []

        # 连线（用经纬度）
        for rel in relations:
            fn, tn = rel.get("from", ""), rel.get("to", "")
            if rel.get("relation") in ("part_of", "capital_of"):
                continue
            if fn not in all_coords or tn not in all_coords:
                continue
            fc, tc = all_coords[fn], all_coords[tn]
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [[fc["lng"], fc["lat"]], [tc["lng"], tc["lat"]]]},
                "properties": {"feature_type": "relation", "relation": rel.get("relation", ""),
                               "distance": rel.get("distance", ""), "from": fn, "to": tn}
            })

        # 地点标记（用经纬度）
        for loc in locations:
            name = loc.get("name", "")
            if name not in all_coords:
                continue
            coord = all_coords[name]
            loc_type = loc.get("type", coord.get("type", "other"))
            related_events = [e for e in events if e.get("location") == name]
            related_chars = [c for c in characters if self._char_at_loc(c, name, events)]
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [coord["lng"], coord["lat"]]},
                "properties": {
                    "feature_type": "location", "name": name, "location_type": loc_type,
                    "description": loc.get("description", ""),
                    "color": LOCATION_COLORS.get(loc_type, LOCATION_COLORS["other"]),
                    "event_count": len(related_events), "character_count": len(related_chars),
                    "events": related_events[:10], "characters": related_chars[:10],
                    "importance": self._importance(name, relations, events),
                    "belongs_to": loc.get("belongs_to", ""),
                }
            })

        # 计算中心点和缩放级别（基于经纬度）
        lngs = [c["lng"] for c in all_coords.values()]
        lats = [c["lat"] for c in all_coords.values()]
        center = [round(sum(lats)/len(lats), 4), round(sum(lngs)/len(lngs), 4)]
        lng_range = max(lngs) - min(lngs)
        lat_range = max(lats) - min(lats)
        max_range = max(lng_range, lat_range)
        if max_range > 20:
            zoom = 4
        elif max_range > 10:
            zoom = 5
        elif max_range > 5:
            zoom = 6
        elif max_range > 2:
            zoom = 7
        else:
            zoom = 8

        return {
            "type": "FeatureCollection", "features": features,
            "metadata": {
                "center": center, "zoom": zoom, "world_type": "real",
                "use_real_map": True,  # ★ 告诉前端使用真实地图
                "stats": {"locations": len(locations), "relations": len(relations),
                          "characters": len(characters), "events": len(events)},
                "region_groups": {k: v for k, v in region_groups.items()},
                "known_count": len(known_coords), "total_count": len(locations),
            }
        }

    # ==================== 虚构世界 ====================
    def _generate_fictional_map(self, locations, relations, characters, events):
        n = len(locations)
        names = [loc["name"] for loc in locations]

        region_groups, region_traits = self._build_region_groups(locations, characters)

        # 力导向布局
        positions = {}
        if region_groups:
            positions = self._init_by_regions(positions, region_groups, names, region_traits)
        else:
            for i, name in enumerate(names):
                a = 2 * math.pi * i / n
                r = 350 + random.uniform(-80, 80)
                positions[name] = [r * math.cos(a) + random.uniform(-50, 50), r * math.sin(a) + random.uniform(-50, 50)]

        positions = self._force_layout(positions, relations, region_groups, iterations=300)
        positions = self._normalize(positions)

        # 给locations添加坐标（用于地形生成）
        locs_with_coords = []
        for loc in locations:
            name = loc.get("name", "")
            if name in positions:
                locs_with_coords.append({**loc, "x": positions[name][0], "y": positions[name][1]})

        # ★ 生成地形底图（传入区域地形特征）
        try:
            terrain_b64 = generate_fictional_terrain(locs_with_coords, region_groups, region_traits=region_traits, size=1200)
        except Exception as e:
            logger.warning(f"地形生成失败: {e}")
            terrain_b64 = None

        # GeoJSON features
        features = []
        for rel in relations:
            fn, tn = rel.get("from", ""), rel.get("to", "")
            if rel.get("relation") in ("part_of", "capital_of"):
                continue
            if fn not in positions or tn not in positions:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "LineString", "coordinates": [positions[fn], positions[tn]]},
                "properties": {"feature_type": "relation", "relation": rel.get("relation", ""),
                               "distance": rel.get("distance", ""), "from": fn, "to": tn}
            })

        for loc in locations:
            name = loc.get("name", "")
            if name not in positions:
                continue
            loc_type = loc.get("type", "other")
            related_events = [e for e in events if e.get("location") == name]
            related_chars = [c for c in characters if self._char_at_loc(c, name, events)]
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": positions[name]},
                "properties": {
                    "feature_type": "location", "name": name, "location_type": loc_type,
                    "description": loc.get("description", ""),
                    "color": LOCATION_COLORS.get(loc_type, LOCATION_COLORS["other"]),
                    "event_count": len(related_events), "character_count": len(related_chars),
                    "events": related_events[:10], "characters": related_chars[:10],
                    "importance": self._importance(name, relations, events),
                    "belongs_to": loc.get("belongs_to", ""),
                }
            })

        all_xy = list(positions.values())
        return {
            "type": "FeatureCollection", "features": features,
            "metadata": {
                "center": self._center(all_xy), "zoom": self._zoom(all_xy),
                "world_type": "fictional",
                "stats": {"locations": n, "relations": len(relations),
                          "characters": len(characters), "events": len(events)},
                "region_groups": region_groups,
                "terrain_image": terrain_b64,
                "map_bounds": [0, 0, 1000, 1000],
            }
        }

    # ==================== 坐标分配 ====================
    def _assign_unknown_coords(self, locations, known_coords, relations):
        all_coords = {}
        for loc in locations:
            name = loc.get("name", "")
            if name in known_coords:
                all_coords[name] = known_coords[name]
            else:
                all_coords[name] = None

        for name in [n for n, c in all_coords.items() if c is None]:
            best_pos = None
            best_score = -1
            for rel in relations:
                other_name = None
                if rel.get("from") == name and rel.get("to") in all_coords and all_coords[rel["to"]]:
                    other_name = rel["to"]
                elif rel.get("to") == name and rel.get("from") in all_coords and all_coords[rel["from"]]:
                    other_name = rel["from"]
                if not other_name:
                    continue
                other = all_coords[other_name]
                relation = rel.get("relation", "")
                offsets = {"north_of": (0, 60), "south_of": (0, -60), "east_of": (60, 0), "west_of": (-60, 0),
                           "near": (random.randint(-40, 40), random.randint(-40, 40)),
                           "adjacent_to": (random.randint(-50, 50), random.randint(-50, 50)),
                           "connected_to": (random.randint(-60, 60), random.randint(-60, 60))}
                if relation in offsets:
                    dx, dy = offsets[relation]
                    score = 2
                else:
                    dx, dy = random.randint(-80, 80), random.randint(-80, 80)
                    score = 1
                pos = {"x": other["x"] + dx, "y": other["y"] + dy, "type": "other", "region": other.get("region", "")}
                if score > best_score:
                    best_pos = pos
                    best_score = score
            if best_pos:
                all_coords[name] = best_pos
            else:
                all_coords[name] = {"x": 500 + random.randint(-100, 100), "y": 400 + random.randint(-100, 100), "type": "other", "region": ""}
        return all_coords

    def _assign_unknown_coords_lnglat(self, locations, known_coords, relations):
        """为未知地点分配经纬度坐标（基于已知地点的相对位置）"""
        all_coords = {}
        for loc in locations:
            name = loc.get("name", "")
            if name in known_coords:
                all_coords[name] = known_coords[name]
            else:
                all_coords[name] = None

        # 经纬度偏移量（度）
        lng_offsets = {"north_of": (0, 1.5), "south_of": (0, -1.5), "east_of": (1.5, 0), "west_of": (-1.5, 0),
                       "near": (random.uniform(-0.5, 0.5), random.uniform(-0.5, 0.5)),
                       "adjacent_to": (random.uniform(-1, 1), random.uniform(-1, 1)),
                       "connected_to": (random.uniform(-1.5, 1.5), random.uniform(-1.5, 1.5))}

        for name in [n for n, c in all_coords.items() if c is None]:
            best_pos = None
            best_score = -1
            for rel in relations:
                other_name = None
                if rel.get("from") == name and rel.get("to") in all_coords and all_coords[rel["to"]]:
                    other_name = rel["to"]
                elif rel.get("to") == name and rel.get("from") in all_coords and all_coords[rel["from"]]:
                    other_name = rel["from"]
                if not other_name:
                    continue
                other = all_coords[other_name]
                relation = rel.get("relation", "")
                if relation in lng_offsets:
                    dlng, dlat = lng_offsets[relation]
                    score = 2
                else:
                    dlng, dlat = random.uniform(-1, 1), random.uniform(-1, 1)
                    score = 1
                pos = {"lng": other["lng"] + dlng, "lat": other["lat"] + dlat,
                       "type": "other", "region": other.get("region", "")}
                if score > best_score:
                    best_pos = pos
                    best_score = score
            if best_pos:
                all_coords[name] = best_pos
            else:
                # 默认放在中国中部
                all_coords[name] = {"lng": 110 + random.uniform(-5, 5),
                                    "lat": 33 + random.uniform(-5, 5),
                                    "type": "other", "region": ""}
        return all_coords

    def _build_region_groups(self, locations, characters):
        groups = defaultdict(list)
        # ★ 同时收集每个区域的地形特征和方位暗示
        region_traits = {}  # {region_name: {"biome": str|None, "direction": str|None}}

        for loc in locations:
            name = loc.get("name", "")
            belongs = loc.get("belongs_to", "").strip()
            loc_type = loc.get("type", "")
            description = loc.get("description", "")
            if belongs:
                groups[belongs].append(name)
                # 从地点type和description中提取地形特征
                if belongs not in region_traits:
                    region_traits[belongs] = {"biome": None, "direction": None}
                # 地形特征：type直接匹配
                biome_map = {"desert": "desert", "sea": "ocean", "river": "ocean",
                             "forest": "forest", "mountain": "mountain", "secret_realm": "secret_realm"}
                if loc_type in biome_map and not region_traits[belongs]["biome"]:
                    region_traits[belongs]["biome"] = biome_map[loc_type]
                # 从description中提取地形关键词
                desc_biomes = {"沙漠": "desert", "戈壁": "desert", "荒漠": "desert",
                               "雪": "snow", "冰": "tundra", "冰川": "tundra",
                               "草原": "grassland", "平原": "plain", "丘陵": "hills",
                               "森林": "forest", "密林": "forest", "沼泽": "swamp",
                               "火山": "volcanic", "海洋": "ocean", "海": "ocean"}
                for kw, biome in desc_biomes.items():
                    if kw in description and not region_traits[belongs]["biome"]:
                        region_traits[belongs]["biome"] = biome
                        break
                # 方位暗示：从description中提取
                dir_keywords = {"南方": "south", "北方": "north", "东方": "east", "西方": "west",
                                "南部": "south", "北部": "north", "东部": "east", "西部": "west",
                                "南端": "south", "北端": "north", "东岸": "east", "西岸": "west"}
                for kw, d in dir_keywords.items():
                    if kw in description and not region_traits[belongs]["direction"]:
                        region_traits[belongs]["direction"] = d
                        break

        factions = detect_factions(locations, characters)
        faction_map = {f.get("source", ""): f["name"] for f in factions}
        for loc in locations:
            name = loc.get("name", "")
            belongs = loc.get("belongs_to", "").strip()
            for key, mapped in faction_map.items():
                if key in belongs or belongs in key:
                    if name not in groups[mapped]:
                        groups[mapped].append(name)
                    # 继承原始区域的地形特征
                    if belongs in region_traits and mapped not in region_traits:
                        region_traits[mapped] = region_traits[belongs]

        return dict(groups), region_traits

    # ==================== 力导向布局（虚构世界用） ====================
    def _init_by_regions(self, positions, region_groups, loc_names, region_traits=None):
        loc_to_region = {}
        for rn, members in region_groups.items():
            for m in members:
                loc_to_region[m] = rn
        regions = list(region_groups.keys())
        if region_traits is None:
            region_traits = {}

        # ★ 区域方位识别：优先从区域名提取，其次从region_traits提取
        REGION_DIR_KEYWORDS = {
            "南": [0, -1],        # 南 → y负方向（地图下方）
            "北": [0, 1],         # 北 → y正方向（地图上方）
            "东": [1, 0],         # 东 → x正方向（地图右方）
            "西": [-1, 0],        # 西 → x负方向（地图左方）
            "中": [0, 0],          # 中心
            "东北": [0.7, 0.7],
            "东南": [0.7, -0.7],
            "西北": [-0.7, 0.7],
            "西南": [-0.7, -0.7],
        }
        # 从region_traits的direction字段映射
        TRAIT_DIR_MAP = {"south": [0, -1], "north": [0, 1], "east": [1, 0], "west": [-1, 0]}

        def get_region_dir(region_name):
            """从区域名中提取方位方向向量"""
            for keyword, direction in REGION_DIR_KEYWORDS.items():
                if keyword in region_name:
                    return direction
            # 其次从region_traits中提取
            traits = region_traits.get(region_name, {})
            trait_dir = traits.get("direction")
            if trait_dir and trait_dir in TRAIT_DIR_MAP:
                return TRAIT_DIR_MAP[trait_dir]
            return None

        # 分配区域中心位置
        region_centers = {}
        used_dirs = []
        for r in regions:
            direction = get_region_dir(r)
            if direction is not None and direction != [0, 0]:
                region_centers[r] = [direction[0] * 350, direction[1] * 350]
                used_dirs.append(tuple(direction))
            elif direction == [0, 0]:
                # "中" → 地图中心
                region_centers[r] = [0, 0]
            else:
                # 无方位信息：均匀分配在剩余角度上
                angle = 2 * math.pi * regions.index(r) / max(len(regions), 1)
                region_centers[r] = [350 * math.cos(angle), 350 * math.sin(angle)]

        for name in loc_names:
            if name in loc_to_region:
                c = region_centers[loc_to_region[name]]
                # ★ 用极坐标均匀散布在区域内，避免直线排列
                angle = random.uniform(0, 2 * math.pi)
                radius = random.uniform(30, 180)
                positions[name] = [c[0] + radius * math.cos(angle), c[1] + radius * math.sin(angle)]
            else:
                # 无区域归属的地点：均匀分布在大圆上，避免扎堆
                a = 2 * math.pi * loc_names.index(name) / len(loc_names)
                r = 350 + random.uniform(-80, 80)
                positions[name] = [r * math.cos(a) + random.uniform(-50, 50), r * math.sin(a) + random.uniform(-50, 50)]
        return positions

    def _force_layout(self, positions, relations, region_groups, iterations=300):
        repulsion, attraction, center_str, dir_weight, region_str, damping = 12000, 0.01, 0.03, 50, 0.015, 0.9
        velocities = {n: [0.0, 0.0] for n in positions}
        dir_map = {"north_of": [0,1], "south_of": [0,-1], "east_of": [1,0], "west_of": [-1,0],
                   "contains": [0,0], "adjacent_to": [0,0], "connected_to": [0,0], "near": [0,0],
                   "part_of": [0,0], "capital_of": [0,0], "located_in": [0,0]}
        names = list(positions.keys())
        n = len(names)

        # ★ 识别哪些地点有方位关系，哪些没有
        locs_with_dir = set()
        for rel in relations:
            rt = rel.get("relation", "")
            if rt in dir_map and dir_map[rt] != [0, 0]:
                locs_with_dir.add(rel.get("from", ""))
                locs_with_dir.add(rel.get("to", ""))

        for _ in range(iterations):
            forces = {n: [0.0, 0.0] for n in positions}
            for i in range(n):
                for j in range(i+1, n):
                    ni, nj = names[i], names[j]
                    dx = positions[ni][0] - positions[nj][0]
                    dy = positions[ni][1] - positions[nj][1]
                    d = math.sqrt(dx*dx + dy*dy) + 0.1
                    f = repulsion / (d*d)
                    fx, fy = f*dx/d, f*dy/d
                    forces[ni][0] += fx; forces[ni][1] += fy
                    forces[nj][0] -= fx; forces[nj][1] -= fy
            for rel in relations:
                fn, tn = rel.get("from",""), rel.get("to","")
                if fn not in positions or tn not in positions: continue
                if rel.get("relation") in ("part_of","capital_of"): continue
                dx = positions[tn][0] - positions[fn][0]
                dy = positions[tn][1] - positions[fn][1]
                d = math.sqrt(dx*dx+dy*dy)+0.1
                f = attraction * (d - 150)
                fx, fy = f*dx/d, f*dy/d
                forces[fn][0] += fx; forces[fn][1] += fy
                forces[tn][0] -= fx; forces[tn][1] -= fy
                rt = rel.get("relation","")
                if rt in dir_map and dir_map[rt] != [0,0]:
                    forces[fn][0] += dir_map[rt][0]*dir_weight
                    forces[fn][1] += dir_map[rt][1]*dir_weight
            if region_groups:
                for rn, members in region_groups.items():
                    rm = [m for m in members if m in positions]
                    if len(rm) < 1: continue
                    cx = sum(positions[m][0] for m in rm) / len(rm)
                    cy = sum(positions[m][1] for m in rm) / len(rm)
                    for m in rm:
                        forces[m][0] += (cx - positions[m][0]) * region_str
                        forces[m][1] += (cy - positions[m][1]) * region_str
                    # ★ "中"区域：额外中心引力，保持在地图中心
                    if any(k in rn for k in ("中",)):
                        for m in rm:
                            forces[m][0] -= positions[m][0] * 0.08
                            forces[m][1] -= positions[m][1] * 0.08
            for name in positions:
                forces[name][0] -= positions[name][0]*center_str
                forces[name][1] -= positions[name][1]*center_str
            # ★ 空隙填充力：无方位关系的地点被推到已有地点的空隙中
            for name in positions:
                if name in locs_with_dir or not locs_with_dir:
                    continue  # 所有地点都有方位关系，或都没有，跳过
                # 找到最近的3个有方位关系的地点，被它们的"重心反方向"推开
                nearby_with_dir = []
                for other in positions:
                    if other == name or other not in locs_with_dir:
                        continue
                    dx = positions[name][0] - positions[other][0]
                    dy = positions[name][1] - positions[other][1]
                    d = math.sqrt(dx*dx + dy*dy) + 0.1
                    nearby_with_dir.append((d, other))
                nearby_with_dir.sort()
                if len(nearby_with_dir) >= 2:
                    # 计算最近2个地点的中点，被推离中点
                    p1, p2 = nearby_with_dir[0][1], nearby_with_dir[1][1]
                    mid_x = (positions[p1][0] + positions[p2][0]) / 2
                    mid_y = (positions[p1][1] + positions[p2][1]) / 2
                    dx = positions[name][0] - mid_x
                    dy = positions[name][1] - mid_y
                    d = math.sqrt(dx*dx + dy*dy) + 0.1
                    # 如果太近就推开
                    if d < 150:
                        push = 30 / d
                        forces[name][0] += dx/d * push
                        forces[name][1] += dy/d * push
            mx = 10
            for name in positions:
                velocities[name][0] = (velocities[name][0]+forces[name][0])*damping
                velocities[name][1] = (velocities[name][1]+forces[name][1])*damping
                vd = math.sqrt(velocities[name][0]**2+velocities[name][1]**2)
                if vd > mx:
                    velocities[name][0] = velocities[name][0]/vd*mx
                    velocities[name][1] = velocities[name][1]/vd*mx
                positions[name][0] += velocities[name][0]
                positions[name][1] += velocities[name][1]
        return positions

    def _normalize(self, positions):
        if not positions: return positions
        xs = [p[0] for p in positions.values()]
        ys = [p[1] for p in positions.values()]
        mnx, mxx = min(xs), max(xs)
        mny, mxy = min(ys), max(ys)
        rx = mxx-mnx if mxx!=mnx else 1
        ry = mxy-mny if mxy!=mny else 1
        mr = max(rx,ry)
        margin = 100
        return {n: [round((x-mnx)/mr*(1000-2*margin)+margin, 2),
                    round((y-mny)/mr*(1000-2*margin)+margin, 2)] for n,(x,y) in positions.items()}

    # ==================== 工具 ====================
    def _center(self, coords):
        if not coords: return [500,500]
        return [round(sum(c[0] for c in coords)/len(coords),2), round(sum(c[1] for c in coords)/len(coords),2)]
    def _zoom(self, coords):
        if not coords: return 1
        xs = [c[0] for c in coords]; ys = [c[1] for c in coords]
        mr = max(max(xs)-min(xs), max(ys)-min(ys))
        return 0.8 if mr>800 else 1.0 if mr>500 else 1.2 if mr>300 else 1.5
    def _importance(self, name, relations, events):
        return sum(1 for r in relations if r.get("from")==name or r.get("to")==name) + sum(2 for e in events if e.get("location")==name)
    def _char_at_loc(self, char, loc_name, events):
        cn = char.get("name","")
        return any(e.get("location")==loc_name and cn in e.get("participants",[]) for e in events)
