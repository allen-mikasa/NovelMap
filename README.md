# NovelMap Engine - 小说地图可视化引擎

> **一卷小说，一张地图**

基于 TRAE SOLO 构建的小说地图可视化引擎。用户只需上传小说文本，AI 即可自动提取地名、空间关系、人物与事件，并生成交互式世界地图（真实地理 / 虚构地形双模式），让每本小说的虚构世界跃然纸上。

---

## 效果展示

### 真实地理模式 —《三国演义》

AI 自动识别真实地名并定位到地图上，点击地点可查看关联事件与人物。

![三国演义 - 真实地理模式]
<img width="2544" height="1457" alt="屏幕截图 2026-04-22 162507" src="https://github.com/user-attachments/assets/3c6bcf5b-49b7-40f6-8ee7-aef7f95c5a04" />


### 虚构世界模式 —《剑来》

为玄幻/仙侠小说生成程序化地形，基于空间关系自动排布地点，支持势力区域着色与生物群落渲染。

![剑来 - 虚构世界模式]
<img width="2450" height="1462" alt="屏幕截图 2026-04-22 162340" src="https://github.com/user-attachments/assets/940c66b3-31b1-4f30-b745-8b4121d3fb62" />

---

## 功能特性

- **AI 智能分析** — 自动提取地名、空间关系、人物、事件，支持长文本分段处理
- **双模式地图** — 真实地理模式（历史小说）+ 虚构世界模式（玄幻仙侠）
- **虚构地形生成** — Perlin Noise 程序化地形 + 生物群落着色（沙漠/雪原/草原/森林等）
- **力导向布局** — 基于空间关系的自动地点排布，支持区域方位识别
- **势力区域可视化** — 凸包多边形 + 鼠标悬浮提示 + 区域名称标注
- **图层控制** — 势力区域 / 关系连线 / 地名标注独立开关
- **文件上传** — 支持 `.txt` / `.md` 格式小说文件，最大 50MB
- **实时进度** — WebSocket 推送 AI 分析进度
- **地点详情** — 点击标记查看描述、关联事件、相关人物

---

## 技术栈

| 层级 | 技术 |
|------|------|
| Backend | Python, FastAPI, WebSocket, 智谱AI GLM-4-Flash |
| Frontend | HTML / CSS / JavaScript, Leaflet.js, CRS.Simple |
| 地形生成 | Perlin Noise, PIL / Pillow, NumPy |
| 布局算法 | Force-directed Layout（力导向布局） |

---

## 项目结构

```
novel-map-demo/
├── app.py                  # FastAPI 主应用，WebSocket 通信
├── ai_engine.py            # AI 分析引擎，文本分段 + 实体提取
├── map_engine.py           # 地图引擎，坐标分配 + 力导向布局
├── terrain_generator.py    # 地形生成器，Perlin Noise + 生物群落
├── geo_database.py         # 地理编码数据库，真实坐标查询
├── templates/
│   └── index.html          # 前端页面，Leaflet 地图渲染
├── static/
│   └── libs/
│       ├── leaflet.css     # Leaflet 样式
│       └── leaflet.js      # Leaflet 库
└── images/
    ├── demo_real_map.png   # 真实地理模式效果图
    └── demo_fictional_map.png  # 虚构世界模式效果图
```

---

## 快速开始

### 前置条件

- Python 3.10+
- 智谱AI API Key（[免费申请](https://open.bigmodel.cn/)，使用 GLM-4-Flash 免费模型）

### 安装依赖

```bash
pip install fastapi uvicorn websockets zhipuai pillow numpy
```

### 启动服务

```bash
cd novel-map-demo
uvicorn app:app --host 0.0.0.0 --port 8000
```

### 使用

1. 打开浏览器访问 `http://localhost:8000`
2. 在顶部输入框填入智谱AI API Key，点击「验证」
3. 粘贴小说文本或点击「上传文件」加载 `.txt` 文件
4. 点击「开始分析」，等待 AI 提取完成
5. 在左侧面板查看分析结果，在地图上浏览交互式世界地图

---

## 开发背景

在学习和生活中，我发现身边的许多同学都有阅读小说的习惯，但是目前的网络小说大多信息量巨大，很容易忘记前文情节，对于小说的世界观也难以精准记忆。于是我便想着，能否打造一款能让小说可视化的工具？

带着这个想法，我开始了 NovelMap Engine 的开发——用户只需上传小说文本，AI 就能自动提取地名、空间关系、人物与事件，并生成一张可交互的世界地图。


---

## License

MIT
