"""
AI分析引擎 - 调用智谱GLM-4-Flash（免费模型）提取小说地理信息
增强版：让LLM直接输出地点坐标和区域边界
"""
import json
import re
import logging
from typing import Optional
from zhipuai import ZhipuAI

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "glm-4-flash"
MAX_CHUNK_SIZE = 3000


class AIEngine:
    def __init__(self, api_key: str):
        self.client = ZhipuAI(api_key=api_key)
        self.model = DEFAULT_MODEL

    def _chat(self, system_prompt: str, user_content: str, temperature: float = 0.1) -> str:
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                temperature=temperature,
                max_tokens=4096,
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            logger.error(f"LLM调用失败: {e}")
            raise RuntimeError(f"LLM调用失败: {str(e)}")

    def _extract_json(self, text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?\s*```', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                pass
        match = re.search(r'\{.*\}', text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        raise ValueError(f"无法从LLM输出中提取JSON: {text[:200]}")

    def split_text(self, text: str) -> list[str]:
        chapters = re.split(r'(第[一二三四五六七八九十百千万零\d]+[章节回卷集部篇]|Chapter\s+\d+)', text)
        chunks = []
        current_chunk = ""
        for i, part in enumerate(chapters):
            if re.match(r'(第[一二三四五六七八九十百千万零\d]+[章节回卷集部篇]|Chapter\s+\d+)', part):
                if current_chunk.strip():
                    chunks.append(current_chunk.strip())
                current_chunk = part
            else:
                current_chunk += part
                while len(current_chunk) > MAX_CHUNK_SIZE:
                    split_pos = current_chunk.rfind('\n', 0, MAX_CHUNK_SIZE)
                    if split_pos < MAX_CHUNK_SIZE * 0.5:
                        split_pos = MAX_CHUNK_SIZE
                    chunks.append(current_chunk[:split_pos].strip())
                    current_chunk = current_chunk[split_pos:].strip()
        if current_chunk.strip():
            chunks.append(current_chunk.strip())
        return chunks if chunks else [text[:MAX_CHUNK_SIZE]]

    def extract_from_chunk(self, chunk_text: str) -> dict:
        """从单个文本块中提取地理信息"""
        system_prompt = """你是一个专业的文学地理信息提取专家。请从小说文本中提取所有地理相关实体和空间关系。

严格按以下JSON格式输出，不要输出任何其他内容：
{
  "locations": [
    {"name": "地名", "type": "city/mountain/river/building/region/forest/desert/sea/secret_realm/other", "description": "简短描述，包含地形特征如沙漠/雪山/草原/沼泽等", "belongs_to": "所属上级区域，如省/州/国/势力名，无则留空"}
  ],
  "spatial_relations": [
    {"from": "地点A", "relation": "关系类型", "to": "地点B", "distance": "距离描述，如无则留空字符串"}
  ],
  "characters": [
    {"name": "人物名", "faction": "所属势力或组织，如无则留空字符串"}
  ],
  "events": [
    {"title": "事件名称", "location": "发生地点", "participants": ["参与者1"], "type": "battle/meeting/discovery/travel/ceremony/other", "description": "简短描述"}
  ]
}

规则：
1. 只提取文本中明确提到的信息，不要推测
2. relation类型（非常重要，请仔细区分）：
   - 方位关系：north_of=在...之北, south_of=在...之南, east_of=在...之东, west_of=在...之西
   - 层级关系：part_of=属于/隶属于, capital_of=是...的首都/都城, located_in=位于...内/在...之中
   - 邻接关系：adjacent_to=相邻/接壤, near=邻近/距离不远, connected_to=相连/相通
   - 包含关系：contains=包含/管辖
3. belongs_to字段：标注地点的行政归属（如"幽州"、"荆州"、"魏国"、"南疆"、"中州"等）
4. 如果文本中没有某类信息，对应数组返回空列表
5. 确保输出是合法的JSON格式
6. ★ 隐式方位推断（非常重要）：如果文本提到"从A出发向南行军到B"，请推断出A在B之北（A north_of B）。类似地：
   - "从A向东到达B" → A west_of B
   - "A在B的南方" → A south_of B
   - "B的北面是A" → A north_of B
   - "从A北上/南下/东进/西征到达B" → 推断对应方位关系
7. ★ 地形特征提取：在description中尽量包含地形关键词，如"沙漠"、"雪山"、"草原"、"沼泽"、"冰原"、"丘陵"、"密林"、"火山"等"""

        result = self._chat(system_prompt, chunk_text)
        return self._extract_json(result)

    def analyze_full_text(self, text: str, progress_callback=None) -> dict:
        """分析完整小说文本，合并所有块的结果"""
        chunks = self.split_text(text)
        total = len(chunks)

        all_locations = []
        all_relations = []
        all_characters = []
        all_events = []

        for i, chunk in enumerate(chunks):
            if progress_callback:
                progress_callback(i, total + 2, f"正在分析第 {i+1}/{total} 段...")
            try:
                result = self.extract_from_chunk(chunk)
                all_locations.extend(result.get("locations", []))
                all_relations.extend(result.get("spatial_relations", []))
                all_characters.extend(result.get("characters", []))
                all_events.extend(result.get("events", []))
            except Exception as e:
                logger.warning(f"第 {i+1} 段分析失败: {e}")
                continue

        merged = self._merge_results(all_locations, all_relations, all_characters, all_events)

        if progress_callback:
            progress_callback(total, total + 2, "提取完成，正在规划地图坐标...")

        return merged

    def plan_map_coordinates(self, analysis_result: dict, progress_callback=None) -> dict:
        """
        关键新方法：让LLM根据所有提取的信息，整体规划每个地点的坐标和区域边界。
        这是解决"地点随机排放"的核心——让LLM利用其地理知识直接给出坐标。
        """
        if progress_callback:
            progress_callback(0, 1, "正在让AI规划地图坐标和区域边界...")

        # 构建给LLM的完整信息摘要
        locations = analysis_result.get("locations", [])
        relations = analysis_result.get("spatial_relations", [])
        characters = analysis_result.get("characters", [])

        loc_summary = ""
        for i, loc in enumerate(locations):
            loc_summary += f"{i+1}. {loc.get('name','')} (类型:{loc.get('type','other')}, 归属:{loc.get('belongs_to','无')}, 描述:{loc.get('description','')})\n"

        rel_summary = ""
        for rel in relations:
            rel_summary += f"  {rel.get('from','')} --[{rel.get('relation','')}]--> {rel.get('to','')} (距离:{rel.get('distance','无')})\n"

        char_summary = ""
        for c in characters:
            char_summary += f"  {c.get('name','')} (势力:{c.get('faction','无')})\n"

        system_prompt = """你是一个专业的地图设计师和地理信息专家。现在需要你为小说中的所有地点规划在一个二维地图上的坐标位置。

你需要根据以下信息，为每个地点分配合理的(x, y)坐标，并划分区域板块边界。

**坐标规则：**
- 使用0-1000的整数坐标范围
- x轴为东西方向（0=最西，1000=最东），y轴为南北方向（0=最南，1000=最北）
- 如果是真实地理（如三国、历史小说），请尽量反映真实的相对位置关系
  - 例如：洛阳应在长安以东偏南，荆州应在长江中游，成都应在西南
- 如果是虚构世界，请根据文本中的方位描述合理布局
- 同一区域/势力内的地点应该聚集在一起
- 不同区域之间应该有明显的间隔

**区域划分规则：**
- 根据belongs_to字段和势力归属，将地点划分为若干区域
- 每个区域用一组顶点坐标定义其边界多边形（至少4个顶点）
- 区域边界应该包围该区域内的所有地点，并留有适当边距
- 不同区域的边界不应该重叠

严格按以下JSON格式输出：
{
  "coordinates": {
    "地点名1": {"x": 500, "y": 300},
    "地点名2": {"x": 200, "y": 600}
  },
  "regions": [
    {
      "name": "区域名称",
      "color_index": 0,
      "members": ["属于该区域的地点名列表"],
      "boundary": [[x1,y1], [x2,y2], [x3,y3], [x4,y4]]
    }
  ]
}

注意：
1. coordinates必须包含所有地点的坐标
2. regions中的boundary是区域边界多边形的顶点列表（按顺时针或逆时针排列）
3. color_index从0开始递增，用于分配颜色
4. 确保输出是合法的JSON格式"""

        user_content = f"""请为以下小说地点规划地图坐标和区域边界：

【地点列表】（共{len(locations)}个）
{loc_summary}

【空间关系】
{rel_summary if rel_summary else '  无'}

【势力/人物】
{char_summary if char_summary else '  无'}

请根据以上信息，合理规划每个地点的坐标位置，并划分区域板块。"""

        result = self._chat(system_prompt, user_content, temperature=0.3)

        if progress_callback:
            progress_callback(1, 1, "地图坐标规划完成")

        return self._extract_json(result)

    def _merge_results(self, locations, relations, characters, events) -> dict:
        seen_locs = {}
        for loc in locations:
            name = loc.get("name", "").strip()
            if name and name not in seen_locs:
                seen_locs[name] = loc
            elif name and loc.get("description"):
                existing = seen_locs[name]
                if not existing.get("description"):
                    existing["description"] = loc["description"]
                elif loc["description"] not in existing["description"]:
                    existing["description"] += "；" + loc["description"]
            # 合并belongs_to
            if name and name in seen_locs and loc.get("belongs_to"):
                existing = seen_locs[name]
                if not existing.get("belongs_to"):
                    existing["belongs_to"] = loc["belongs_to"]

        seen_chars = {}
        for char in characters:
            name = char.get("name", "").strip()
            if name and name not in seen_chars:
                seen_chars[name] = char
            elif name and char.get("faction"):
                existing = seen_chars[name]
                if not existing.get("faction"):
                    existing["faction"] = char["faction"]

        seen_events = {}
        for evt in events:
            title = evt.get("title", "").strip()
            if title and title not in seen_events:
                seen_events[title] = evt

        seen_relations = []
        relation_keys = set()
        for rel in relations:
            key = (rel.get("from", ""), rel.get("relation", ""), rel.get("to", ""))
            if key not in relation_keys and key[0] and key[2]:
                relation_keys.add(key)
                seen_relations.append(rel)

        return {
            "locations": list(seen_locs.values()),
            "spatial_relations": seen_relations,
            "characters": list(seen_chars.values()),
            "events": list(seen_events.values()),
        }

    def classify_world_type(self, locations: list) -> str:
        if not locations:
            return "fictional"
        real_place_keywords = [
            "北京", "上海", "广州", "深圳", "成都", "杭州", "南京", "武汉", "西安",
            "重庆", "天津", "苏州", "长沙", "郑州", "青岛", "大连", "沈阳", "哈尔滨",
            "济南", "福州", "厦门", "昆明", "贵阳", "南宁", "海口", "石家庄", "太原",
            "合肥", "南昌", "长春", "内蒙古", "拉萨", "乌鲁木齐", "兰州", "银川", "西宁",
            "中国", "华夏", "长江", "黄河", "泰山", "华山", "黄山", "嵩山",
            "洛阳", "开封", "襄阳", "荆州", "徐州", "扬州",
            "蜀", "吴", "魏", "晋", "楚", "燕", "齐", "赵", "秦", "汉",
            "长安", "建业", "许昌", "邺城", "成都", "南阳",
            "赤壁", "官渡", "淝水", "巨鹿", "虎牢关", "潼关",
            "幽州", "冀州", "青州", "兖州", "徐州", "豫州", "扬州", "荆州",
            "益州", "凉州", "并州", "雍州", "交州",
        ]
        real_count = 0
        total_count = len(locations)
        for loc in locations:
            name = loc.get("name", "")
            belongs = loc.get("belongs_to", "")
            for keyword in real_place_keywords:
                if keyword in name or keyword in belongs:
                    real_count += 1
                    break
        if total_count == 0:
            return "fictional"
        ratio = real_count / total_count
        if ratio >= 0.5:
            return "real"
        elif ratio <= 0.2:
            return "fictional"
        else:
            return "mixed"
