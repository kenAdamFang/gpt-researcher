# 本地文档研究工作流程分析

本文档详细梳理了当用户选择 "My Documents" 作为数据来源时，GPT-Researcher 框架从接收任务到生成报告的完整内部工作流程。

## 阶段一：前端交互与任务发起

1.  **用户操作**:
    *   用户在浏览器中打开 GPT-Researcher 的前端页面。
    *   在研究任务输入框中，输入要研究的主题，例如“人工智能在医疗领域的应用”。
    *   在数据来源选项中，选择 "My Documents"。
    *   点击 "Begin Research" 按钮。

2.  **代码对应**:
    *   前端逻辑 (`frontend/scripts.js` 或 Next.js 对应文件) 会捕获这些输入。
    *   通过 WebSocket 连接 (`/ws`) 或 HTTP POST 请求 (`/report/`)，将一个包含 `{"task": "...", "report_type": "...", "report_source": "local"}` 的 JSON 对象发送到后端。

## 阶段二：后端接收与任务分发

1.  **接收请求**:
    *   FastAPI 后端应用 (`backend/server/server.py`) 接收到请求。
    *   无论是通过 WebSocket 还是 HTTP POST，最终都会调用 `run_agent` 函数。

2.  **启动研究代理**:
    *   `run_agent` 函数被调用，它会创建一个 `GPTResearcher` 类的实例。
    *   关键参数 `report_source` 被设置为字符串 `"local"`。

3.  **代码对应**:
    *   `backend/server/server.py`: `websocket_endpoint` 或 `generate_report` -> `run_agent`
    *   `run_agent` -> `GPTResearcher(..., report_source="local", ...)`

## 阶段三：研究核心流程

1.  **`GPTResearcher` 初始化与 `conduct_research` 调用**:
    *   `gpt_researcher/agent.py` 中的 `GPTResearcher` 类被实例化，`self.report_source` 属性被设置为 `"local"`。
    *   `self.research_conductor.conduct_research()` 方法被调用。

2.  **进入本地文件处理分支**:
    *   在 `ResearchConductor.conduct_research` (`gpt_researcher/skills/researcher.py`) 方法内部，代码逻辑会进入 `elif self.researcher.report_source == ReportSource.Local.value:` 分支。

3.  **加载本地文档 (关键步骤)**:
    *   `DocumentLoader(self.researcher.cfg.doc_path).load()` 被调用。
    *   `DocumentLoader` (`gpt_researcher/document/document.py`) 遍历由 `.env` 文件中 `DOC_PATH` 指定的目录（默认为 `./my-docs`）。
    *   它会根据文件扩展名（如 `.pdf`, `.txt`, `.docx`）选择合适的 `langchain` 加载器，异步加载所有文件内容。
    *   所有文档的文本内容被加载到一个名为 `document_data` 的列表中。

4.  **生成研究计划和子查询**:
    *   调用 `_get_context_by_web_search(self.researcher.query, document_data, ...)`。虽然方法名带有 "web_search"，但因为传入了 `document_data`，其行为会切换到本地文档分析模式。
    *   内部会调用 `self.plan_research`，它会利用大语言模型（LLM）根据主任务生成一个研究大纲和一系列的子查询（sub_queries）。

5.  **上下文检索 (关键步骤)**:
    *   对每一个子查询，调用 `_process_sub_query(sub_query, document_data, ...)`。
    *   由于 `document_data` 已存在，所有网络爬取相关的步骤都会被跳过。
    *   核心调用 `self.researcher.context_manager.get_similar_content_by_query(sub_query, document_data)`。
    *   `ContextManager` (`gpt_researcher/skills/context_manager.py`) 会调用 `ContextCompressor`，后者执行向量相似度搜索，找出与当前子查询最相关的文档内容块。

6.  **整合上下文**:
    *   所有子查询检索到的相关文本块被收集起来，整合成一个大的研究上下文 `self.researcher.context`，准备用于最终的报告生成。

## 阶段四：报告生成与返回

1.  **生成报告**:
    *   调用 `self.write_report()` 方法。
    *   `ReportGenerator` (`gpt_researcher/skills/writer.py`) 会将任务要求和研究上下文一起打包成一个 Prompt，发送给内网部署的大语言模型（LLM）。
    *   LLM 严格依据提供的上下文信息，撰写并返回研究报告。

2.  **返回结果**:
    *   生成的报告（Markdown 格式）通过 WebSocket 实时流式传输到前端，或在 HTTP 请求完成后一次性返回。
    *   前端将接收到的 Markdown 渲染成用户可见的报告。

这个流程清晰地说明了在选择 "My Documents" 时，框架如何完全依赖本地文件，通过文档加载、子查询生成、向量相似度检索和 LLM 内容生成，最终完成一份研究报告，全程无需访问外部互联网（除了调用内网部署的 LLM API）。
