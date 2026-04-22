"""
NovelMap Engine - FastAPI后端服务
MVP Demo: 小说地图可视化引擎
"""
import json
import logging
import asyncio
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from ai_engine import AIEngine
from map_engine import MapEngine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="NovelMap Engine", version="0.1.0 MVP")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件
app.mount("/static", StaticFiles(directory="static"), name="static")

# 存储分析状态
analysis_tasks = {}


class AnalyzeRequest(BaseModel):
    text: str
    api_key: str


class ApiKeyRequest(BaseModel):
    api_key: str


# ==================== 页面路由 ====================

@app.get("/", response_class=HTMLResponse)
async def index():
    """返回前端页面"""
    with open("templates/index.html", "r", encoding="utf-8") as f:
        return f.read()


# ==================== API路由 ====================

@app.post("/api/validate-key")
async def validate_key(req: ApiKeyRequest):
    """验证API Key是否有效"""
    try:
        engine = AIEngine(api_key=req.api_key)
        # 发送一个简单的测试请求
        response = engine._chat("你是一个测试助手。", "回复OK", temperature=0)
        if "OK" in response or len(response) > 0:
            return {"valid": True, "model": engine.model}
        return {"valid": False, "error": "模型响应异常"}
    except Exception as e:
        return {"valid": False, "error": str(e)}


@app.websocket("/ws/analyze")
async def websocket_analyze(websocket: WebSocket):
    """WebSocket接口：实时分析小说文本并生成地图"""
    await websocket.accept()

    try:
        # 接收配置
        config = await websocket.receive_text()
        config_data = json.loads(config)
        api_key = config_data.get("api_key", "")
        text = config_data.get("text", "")

        if not api_key:
            await websocket.send_json({"type": "error", "message": "请提供API Key"})
            return

        if not text or len(text.strip()) < 50:
            await websocket.send_json({"type": "error", "message": "文本内容太短，请输入至少50个字"})
            return

        # 初始化引擎
        try:
            ai_engine = AIEngine(api_key=api_key)
        except Exception as e:
            await websocket.send_json({"type": "error", "message": f"API Key无效: {str(e)}"})
            return

        map_engine = MapEngine()

        # 进度回调
        async def progress_callback(current, total, message):
            await websocket.send_json({
                "type": "progress",
                "current": current,
                "total": total,
                "message": message,
                "percent": round(current / total * 100) if total > 0 else 0,
            })

        # 发送开始信号
        await websocket.send_json({"type": "status", "message": "开始分析..."})

        # 在线程池中运行同步的AI分析
        loop = asyncio.get_event_loop()

        def run_analysis():
            result = ai_engine.analyze_full_text(
                text,
                progress_callback=lambda c, t, m: asyncio.run_coroutine_threadsafe(
                    progress_callback(c, t, m), loop
                ).result()
            )
            return result

        try:
            analysis_result = await loop.run_in_executor(None, run_analysis)
        except Exception as e:
            await websocket.send_json({"type": "error", "message": f"分析失败: {str(e)}"})
            return

        # 发送分析结果摘要
        stats = {
            "locations": len(analysis_result.get("locations", [])),
            "relations": len(analysis_result.get("spatial_relations", [])),
            "characters": len(analysis_result.get("characters", [])),
            "events": len(analysis_result.get("events", [])),
        }
        await websocket.send_json({"type": "analysis_done", "stats": stats, "data": analysis_result})

        # 判断世界类型
        world_type = ai_engine.classify_world_type(analysis_result.get("locations", []))
        await websocket.send_json({"type": "status", "message": f"世界类型: {world_type}，正在生成地图..."})

        # 生成地图（真实地理用内置数据库坐标，虚构世界用力导向布局）
        def run_map_gen():
            return map_engine.generate_map(analysis_result, world_type)

        map_data = await loop.run_in_executor(None, run_map_gen)

        # 发送地图数据
        await websocket.send_json({"type": "map_ready", "map_data": map_data})
        await websocket.send_json({"type": "done", "message": "地图生成完成！"})

    except WebSocketDisconnect:
        logger.info("WebSocket连接断开")
    except json.JSONDecodeError:
        await websocket.send_json({"type": "error", "message": "无效的JSON格式"})
    except Exception as e:
        logger.error(f"WebSocket处理异常: {e}")
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except:
            pass


@app.get("/api/demo-data")
async def get_demo_data():
    """获取内置演示数据（用于无API Key时展示）"""
    demo_data = {
        "locations": [
            {"name": "天启城", "type": "city", "description": "天启帝国首都，位于大陆中央，人口百万", "belongs_to": "天启帝国"},
            {"name": "黑风山", "type": "mountain", "description": "位于大陆东南部的险峻山脉，常年黑雾弥漫", "belongs_to": "天启帝国"},
            {"name": "碧波湖", "type": "river", "description": "天启城以北三百里的大型湖泊", "belongs_to": "天启帝国"},
            {"name": "幽暗森林", "type": "forest", "description": "大陆西部的原始森林，传说中有精灵族居住", "belongs_to": "精灵领"},
            {"name": "赤焰沙漠", "type": "desert", "description": "大陆南部的无尽沙漠，极端炎热", "belongs_to": "蛮荒之地"},
            {"name": "冰霜要塞", "type": "building", "description": "大陆北方的军事要塞，抵御北方蛮族", "belongs_to": "冰原联盟"},
            {"name": "龙门港", "type": "city", "description": "大陆东海岸的繁华港口城市", "belongs_to": "天启帝国"},
            {"name": "万灵秘境", "type": "secret_realm", "description": "传说中的上古秘境，蕴含天地灵气", "belongs_to": "精灵领"},
            {"name": "落日平原", "type": "region", "description": "天启城与黑风山之间的广袤平原", "belongs_to": "天启帝国"},
            {"name": "星陨谷", "type": "mountain", "description": "幽暗森林深处的神秘峡谷", "belongs_to": "万灵秘境"},
            {"name": "炎魔堡垒", "type": "building", "description": "赤焰沙漠深处的古代遗迹", "belongs_to": "蛮荒之地"},
            {"name": "雪峰关", "type": "building", "description": "冰原联盟的南方门户", "belongs_to": "冰原联盟"},
        ],
        "spatial_relations": [
            {"from": "黑风山", "relation": "east_of", "to": "天启城", "distance": "三千里"},
            {"from": "碧波湖", "relation": "north_of", "to": "天启城", "distance": "三百里"},
            {"from": "幽暗森林", "relation": "west_of", "to": "天启城", "distance": "两千里"},
            {"from": "赤焰沙漠", "relation": "south_of", "to": "天启城", "distance": "五千里"},
            {"from": "冰霜要塞", "relation": "north_of", "to": "碧波湖", "distance": "一千里"},
            {"from": "龙门港", "relation": "east_of", "to": "黑风山", "distance": "八百里"},
            {"from": "万灵秘境", "relation": "contains", "to": "星陨谷", "distance": ""},
            {"from": "落日平原", "relation": "adjacent_to", "to": "天启城", "distance": ""},
            {"from": "万灵秘境", "relation": "near", "to": "幽暗森林", "distance": "五百里"},
            {"from": "赤焰沙漠", "relation": "south_of", "to": "落日平原", "distance": "两千里"},
            {"from": "炎魔堡垒", "relation": "located_in", "to": "赤焰沙漠", "distance": ""},
            {"from": "雪峰关", "relation": "south_of", "to": "冰霜要塞", "distance": "五百里"},
        ],
        "characters": [
            {"name": "李逍遥", "faction": "天启帝国"},
            {"name": "赵灵儿", "faction": "仙灵岛"},
            {"name": "林月如", "faction": "武林盟"},
            {"name": "酒剑仙", "faction": "蜀山派"},
            {"name": "拜月教主", "faction": "拜月教"},
        ],
        "events": [
            {"title": "天启城保卫战", "location": "天启城", "participants": ["李逍遥", "林月如"], "type": "battle", "description": "拜月教大军围攻天启城"},
            {"title": "黑风山论剑", "location": "黑风山", "participants": ["李逍遥", "酒剑仙"], "type": "meeting", "description": "天下剑客齐聚黑风山"},
            {"title": "万灵秘境探险", "location": "万灵秘境", "participants": ["李逍遥", "赵灵儿"], "type": "discovery", "description": "发现上古神器"},
            {"title": "冰霜要塞之战", "location": "冰霜要塞", "participants": ["林月如"], "type": "battle", "description": "抵御北方蛮族入侵"},
            {"title": "龙门港海战", "location": "龙门港", "participants": ["李逍遥", "拜月教主"], "type": "battle", "description": "海上决战"},
        ],
    }

    map_engine = MapEngine()
    map_data = map_engine.generate_map(demo_data, "fictional")
    return {"analysis": demo_data, "map": map_data}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
