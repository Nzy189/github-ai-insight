# 报告页信息架构改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 HTML 报告页从"先给评分再讲内容"改成"一句话说清是什么 → 三行讲透痛点/方案/能不能用 → 评分退居佐证"，让读者在手机首屏 10 秒内判断要不要装。

**Architecture:** 分两层改造。内容层给 LLM 输出加一个 `tldr` 嵌套对象（pain / solution / fit 三个短句），并把 `one_liner` 从软建议改成带反面示例的硬约束；呈现层重构 Jinja2 模板的 Hero 区、重排首屏以下四个区块、把 2×2 评分卡压成四行细条。两层都必须容忍字段缺失——`tldr` 逐字段兜底，缺失的行在模板里整行不渲染。

**Tech Stack:** Python 3.11+ / pydantic-settings / Jinja2 / pytest / 无 JS 自包含 HTML

## Global Constraints

- 所有字号不得低于 **11px**（DESIGN.md Nano 层级下限）。这是本次否掉 B1 方案的理由，实现时不得自己再犯。
- 报告页保持**零 JS、零外部请求、单文件自包含**。不得引入折叠面板、标签页或任何脚本。
- 色彩、间距、阴影、圆角一律沿用 DESIGN.md 现有 token，本次只改字号层级与两个区块的排布。
- **不回溯重渲染历史报告**，`data/reports/` 下旧 HTML 保持原样，数据库旧记录不迁移。
- **不改评分权重**，`SCORE_WEIGHTS` 维持 35/30/25/10。
- **不改企微推送消息格式**，`wechat_notifier.py` 本次不动。
- 旧数据（`ai_summary` JSON 中无 `tldr` 键）必须能正常渲染，不得抛异常。
- 提交信息结尾统一附 `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`。

## 对 spec 的一处修正

spec §4.3 写「`pain` 缺失 → `repo.description`；`solution` 缺失 → `repo.description`」。照此实现，两个字段都缺失时首屏会把同一句话印两遍。

**实现时改为**：`pain` 优先取 `description`；`solution` 只在 `pain` 没有消耗掉 `description` 时才回退到它，否则留空由模板隐藏整行。其余兜底规则与 spec 一致。

## 文件结构

| 文件 | 本次职责 |
|---|---|
| `models.py` | 新增 `Tldr` dataclass；`Analysis` 挂载 `tldr` 字段并纳入 `as_dict()` |
| `ai_analyzer.py` | 新增 `normalize_tldr()`；`normalize_analysis()` 调用它；`build_degraded_analysis()` 填充 tldr；Prompt 加两节硬约束 |
| `mock_data.py` | 五条假回复补 `tldr`，其中一条故意残缺以覆盖兜底分支 |
| `report_generator.py` | 评分环几何常量由 80px 改 52px；`build_context()` 透出 tldr 行列表 |
| `templates/report.html.j2` | Hero 重构 + 区块重排 + 评分区压扁 + CSS 调整 |
| `DESIGN.md` | 同步三处偏离 |
| `tests/test_config_and_models.py` | `Tldr` 模型行为 |
| `tests/test_ai_analyzer.py` | tldr 解析、兜底、降级、Prompt 内容 |
| `tests/test_report_and_notifier.py` | 模板渲染、区块顺序、空行隐藏、XSS |

---

### Task 1: Tldr 数据模型与全部兜底路径

**Files:**
- Modify: `models.py:86-112`（`Analysis` 定义与 `as_dict`）
- Modify: `ai_analyzer.py:111-140`（`build_degraded_analysis`）
- Modify: `ai_analyzer.py:188-225`（`normalize_analysis`）
- Test: `tests/test_config_and_models.py`
- Test: `tests/test_ai_analyzer.py`

**Interfaces:**
- Produces: `models.Tldr(pain: str, solution: str, fit: str)`，含 `as_dict() -> dict[str, str]` 与 `is_empty: bool` 属性
- Produces: `Analysis.tldr: Tldr`，默认 `Tldr()`
- Produces: `ai_analyzer.normalize_tldr(payload: dict[str, Any], repo: Repo) -> Tldr`
- Consumes: 现有 `ai_analyzer._as_str(value, default="") -> str`

- [ ] **Step 1: 写失败测试 —— Tldr 模型**

追加到 `tests/test_config_and_models.py` 末尾：

```python
from models import Tldr


class TestTldr:
    def test_defaults_are_empty(self):
        t = Tldr()
        assert t.pain == ""
        assert t.solution == ""
        assert t.fit == ""
        assert t.is_empty is True

    def test_is_empty_false_when_any_field_set(self):
        assert Tldr(pain="x").is_empty is False
        assert Tldr(solution="x").is_empty is False
        assert Tldr(fit="x").is_empty is False

    def test_as_dict(self):
        t = Tldr(pain="痛", solution="解", fit="配")
        assert t.as_dict() == {"pain": "痛", "solution": "解", "fit": "配"}

    def test_analysis_carries_tldr(self, analysis):
        assert isinstance(analysis.tldr, Tldr)
        assert "tldr" in analysis.as_dict()
        assert analysis.as_dict()["tldr"] == analysis.tldr.as_dict()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_config_and_models.py::TestTldr -v`
Expected: FAIL — `ImportError: cannot import name 'Tldr' from 'models'`

- [ ] **Step 3: 实现 Tldr 模型**

在 `models.py` 中 `Scores` 类之后、`Analysis` 之前插入：

```python
@dataclass(slots=True)
class Tldr:
    """首屏三要素 —— 痛点 / 怎么解决 / 我能用吗。

    任一字段允许为空：模板会整行隐藏，而不是渲染出一个空壳。
    """

    pain: str = ""
    solution: str = ""
    fit: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"pain": self.pain, "solution": self.solution, "fit": self.fit}

    @property
    def is_empty(self) -> bool:
        return not (self.pain or self.solution or self.fit)
```

把 `Analysis` 的字段列表改成（在 `scores` 之后加一行）：

```python
    scores: Scores = field(default_factory=Scores)
    tldr: Tldr = field(default_factory=Tldr)
    degraded: bool = False
```

把 `Analysis.as_dict()` 改成：

```python
    def as_dict(self) -> dict[str, Any]:
        return {
            "one_liner": self.one_liner,
            "tldr": self.tldr.as_dict(),
            "highlights": self.highlights,
            "target_audience": self.target_audience,
            "difficulty": self.difficulty,
            "rating": self.rating,
            "rating_reason": self.rating_reason,
            "detailed_intro": self.detailed_intro,
            "scores": self.scores.as_dict(),
        }
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python -m pytest tests/test_config_and_models.py::TestTldr -v`
Expected: PASS，4 个用例全绿

- [ ] **Step 5: 写失败测试 —— normalize_tldr 解析与兜底**

追加到 `tests/test_ai_analyzer.py` 末尾：

```python
from ai_analyzer import normalize_tldr
from models import Tldr

FULL_TLDR = {
    "pain": "平台已经能检测出你的稿子是 AI 写的",
    "solution": "三层清理：隐形字符、文本水印、元数据",
    "fit": "纯 Python 标准库，不用 Docker",
}


class TestNormalizeTldr:
    def test_full_payload(self, repo):
        t = normalize_tldr({"tldr": FULL_TLDR}, repo)
        assert t.pain == FULL_TLDR["pain"]
        assert t.solution == FULL_TLDR["solution"]
        assert t.fit == FULL_TLDR["fit"]

    def test_missing_key_entirely(self, repo):
        """整个 tldr 缺失 → pain 取 description，solution 留空避免重复。"""
        t = normalize_tldr({}, repo)
        assert t.pain == repo.description
        assert t.solution == ""

    @pytest.mark.parametrize("bad", ["一段字符串", 42, None, ["a", "b"]])
    def test_wrong_type_does_not_raise(self, repo, bad):
        t = normalize_tldr({"tldr": bad}, repo)
        assert isinstance(t, Tldr)

    def test_partial_only_pain(self, repo):
        t = normalize_tldr({"tldr": {"pain": "只有痛点"}}, repo)
        assert t.pain == "只有痛点"
        # pain 没消耗 description，solution 可以回退到它
        assert t.solution == repo.description
        assert t.fit == ""

    def test_solution_not_duplicated_with_pain(self, repo):
        """pain 已回退到 description 时，solution 不得再印同一句。"""
        t = normalize_tldr({"tldr": {"fit": "有 Docker"}}, repo)
        assert t.pain == repo.description
        assert t.solution == ""

    def test_fit_inferred_from_docker_topic(self):
        r = Repo(full_name="a/b", html_url="u", description="d", topics=["ai", "Docker"])
        assert normalize_tldr({}, r).fit == "仓库标注了 Docker 支持"

    def test_fit_left_empty_without_docker_topic(self, repo):
        assert normalize_tldr({}, repo).fit == ""

    def test_normalize_analysis_populates_tldr(self, repo):
        a = normalize_analysis({**GOOD, "tldr": FULL_TLDR}, repo)
        assert a.tldr.pain == FULL_TLDR["pain"]

    def test_old_payload_without_tldr_still_works(self, repo):
        """数据库里的旧记录没有 tldr 键 —— 不能抛异常。"""
        a = normalize_analysis(GOOD, repo)
        assert isinstance(a.tldr, Tldr)


class TestDegradedTldr:
    def test_degraded_fills_all_three(self, repo):
        a = build_degraded_analysis(repo, "LLM 超时")
        assert a.tldr.pain == repo.description
        assert repo.language in a.tldr.solution
        assert "README" in a.tldr.fit

    def test_degraded_fit_does_not_fabricate(self, repo):
        """降级时不许编造部署结论。"""
        fit = build_degraded_analysis(repo, "x").tldr.fit
        assert "AI 分析不可用" in fit
        assert "Docker" not in fit

    def test_degraded_pain_handles_empty_description(self):
        r = Repo(full_name="a/b", html_url="u", description="")
        assert build_degraded_analysis(r, "x").tldr.pain == "该仓库未填写描述"
```

- [ ] **Step 6: 运行测试确认失败**

Run: `python -m pytest tests/test_ai_analyzer.py::TestNormalizeTldr tests/test_ai_analyzer.py::TestDegradedTldr -v`
Expected: FAIL — `ImportError: cannot import name 'normalize_tldr'`

- [ ] **Step 7: 实现 normalize_tldr**

在 `ai_analyzer.py` 的 `normalize_analysis` 定义之前插入：

```python
def normalize_tldr(payload: dict[str, Any], repo: Repo) -> Tldr:
    """解析首屏三要素，逐字段兜底。

    模型漏一个字段不该毁掉整页，所以这里不整体降级。
    solution 只在 pain 没有消耗掉 description 时才回退到它 ——
    否则首屏会把同一句话印两遍。
    """
    raw = payload.get("tldr")
    if not isinstance(raw, dict):
        raw = {}

    description = repo.description.strip()

    pain = _as_str(raw.get("pain")) or description
    used_description_for_pain = bool(description) and pain == description

    solution = _as_str(raw.get("solution"))
    if not solution and not used_description_for_pain:
        solution = description

    fit = _as_str(raw.get("fit"))
    if not fit and any(t.lower() == "docker" for t in repo.topics):
        fit = "仓库标注了 Docker 支持"

    return Tldr(pain=pain, solution=solution, fit=fit)
```

把 `ai_analyzer.py` 顶部的 import 改成：

```python
from models import Analysis, AnalyzedProject, Repo, Scores, Tldr
```

在 `normalize_analysis` 的 `return Analysis(...)` 中加一行（放在 `scores=scores,` 之后）：

```python
        scores=scores,
        tldr=normalize_tldr(payload, repo),
        raw_json=json.dumps(payload, ensure_ascii=False, indent=2),
```

- [ ] **Step 8: 实现降级路径的 tldr**

在 `build_degraded_analysis` 的 `return Analysis(...)` 中，`scores=Scores(...)` 之后加：

```python
        tldr=Tldr(
            pain=repo.description.strip() or "该仓库未填写描述",
            solution=(
                f"主语言 {repo.language or '未知'}"
                + (f"，Topics: {', '.join(repo.topics[:3])}" if repo.topics else "")
            ),
            fit="AI 分析不可用，部署要求请查看仓库 README",
        ),
        degraded=True,
```

- [ ] **Step 9: 运行全部测试**

Run: `python -m pytest -q`
Expected: PASS，全部通过（原 152 + 新增 16 = 168）

- [ ] **Step 10: 提交**

```bash
git add models.py ai_analyzer.py tests/test_config_and_models.py tests/test_ai_analyzer.py
git commit -m "feat: 新增 tldr 首屏三要素字段与逐字段兜底

pain / solution / fit 三个短句支撑报告首屏的新结构。任一字段缺失
只影响该行，不整体降级；solution 不会与 pain 重复印出同一句
description。LLM 完全不可用时 fit 明说'请查看仓库 README'而不
编造部署结论。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Prompt 硬约束与假数据同步

**Files:**
- Modify: `ai_analyzer.py:42-60`（JSON 输出示例）
- Modify: `ai_analyzer.py:62-79`（评分标准之后追加两节写法说明）
- Modify: `mock_data.py:83-210`（五条假回复）
- Test: `tests/test_ai_analyzer.py`

**Interfaces:**
- Consumes: Task 1 的 `normalize_tldr`、`Tldr`
- Produces: `AIAnalyzer.build_prompt(repo)` 输出中包含 `一句话总结（one_liner）的写法` 与 `首屏三要素（tldr）的写法` 两节标题
- Produces: `mock_data.MOCK_LLM_RESPONSES` 每条含 `tldr`（`edge-cases/promptforge` 除外，故意残缺）

- [ ] **Step 1: 写失败测试 —— Prompt 内容**

追加到 `tests/test_ai_analyzer.py`：

```python
class TestPromptHardConstraints:
    def _prompt(self, repo):
        return AIAnalyzer(None).build_prompt(repo)

    def test_declares_tldr_in_json_schema(self, repo):
        p = self._prompt(repo)
        assert '"tldr"' in p
        assert '"pain"' in p
        assert '"solution"' in p
        assert '"fit"' in p

    def test_has_one_liner_writing_section(self, repo):
        assert "一句话总结（one_liner）的写法" in self._prompt(repo)

    def test_has_tldr_writing_section(self, repo):
        assert "首屏三要素（tldr）的写法" in self._prompt(repo)

    def test_shows_negative_example_verbatim(self, repo):
        """反面示例必须原样给出 —— 抽象地说'不要名词堆叠'模型听不懂。"""
        p = self._prompt(repo)
        assert "多供应商AI水印移除工具" in p
        assert "名词短语堆叠" in p

    def test_shows_positive_example(self, repo):
        assert "AI 写的东西会被偷偷打上隐形标记" in self._prompt(repo)

    def test_forbids_empty_fit_phrasing(self, repo):
        assert "适合自托管用户" in self._prompt(repo)

    def test_prompt_still_formats_without_keyerror(self, repo):
        """新增小节里若混入未转义的花括号，.format() 会炸。"""
        p = self._prompt(repo)
        assert repo.full_name in p
        assert str(repo.stars) in p
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_ai_analyzer.py::TestPromptHardConstraints -v`
Expected: FAIL — `assert '"tldr"' in p`

- [ ] **Step 3: 改 JSON 输出示例**

把 `ai_analyzer.py` 中 `USER_PROMPT_TEMPLATE` 的 JSON 块前两行替换为：

```
{{
  "one_liner": "一句人话总结，完整句子且带使用场景，30 字以内。见下方写法要求",
  "tldr": {{
    "pain": "不用它会怎样，具体场景，40 字以内",
    "solution": "它具体干了什么，讲人话，40 字以内",
    "fit": "部署事实：依赖 / 内存 / 要不要 Docker / 要不要显卡，40 字以内"
  }},
  "highlights": ["核心技术亮点1", "核心技术亮点2", "核心技术亮点3"],
```

注意 `tldr` 的花括号在 `.format()` 模板里必须写成 `{{` 和 `}}`。

- [ ] **Step 4: 追加两节写法说明**

在 `USER_PROMPT_TEMPLATE` 的 `## 评分标准` 小节之后、`## 详细介绍的写法` 之前插入：

```
## 一句话总结（one_liner）的写法

**硬性要求：必须是完整句子，必须带使用场景，30 字以内。**
读者读完这一句就要能复述"这东西是干嘛的"。

反面示例（禁止这样写）：
"多供应商AI水印移除工具，清理文本与文件元数据以保护隐私"
这是名词短语堆叠 —— 读者读完仍不知道自己什么时候会用到它。

正面示例：
"AI 写的东西会被偷偷打上隐形标记 —— 这工具帮你洗干净"
有场景、有动作、是完整句子。

## 首屏三要素（tldr）的写法

三句各 40 字以内，缺一不可：

- pain —— 不用它会怎样。必须是具体场景，不许写抽象名词
- solution —— 它具体干了什么。讲人话，不要罗列 API 名或依赖名
- fit —— 部署事实。必须包含以下至少一项：语言与运行时依赖、
  内存或存储占用、是否提供 Docker、是否需要显卡。
  禁止写"适合自托管用户"这类没有信息量的话
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_ai_analyzer.py::TestPromptHardConstraints -v`
Expected: PASS，7 个用例全绿

- [ ] **Step 6: 给假数据补 tldr**

在 `mock_data.py` 的 `MOCK_LLM_RESPONSES` 中，为每条 `_payload(...)` 调用加 `tldr` 参数。四条完整的：

`localstack-ai/agentmesh`：
```python
        tldr={
            "pain": "想串起几个 Agent 协同干活，最后写出一堆胶水代码还难排查",
            "solution": "写一个 YAML 描述任务图，调度、重试、超时全交给运行时",
            "fit": "单个 Go 二进制，镜像不到 40MB，2GB 内存的 NAS 能跑",
        },
```

`quietlabs/ragfoundry`：
```python
        tldr={
            "pain": "调 RAG 参数每改一次就要重跑索引，一下午就没了",
            "solution": "把整条流水线做成可视化节点，改完点一下就重跑并出评估对比",
            "fit": "有 Docker Compose，但本地 Embedding 模型内存占用可观",
        },
```

`nano-tools/whisperbox`（注意这条外面包着 ```json 代码块，改的是里层 `_payload`）：
```python
        tldr={
            "pain": "没显卡的话，Whisper 转写一小时录音要等大半天",
            "solution": "Rust 重写推理链路并做 CPU 量化，还能监听目录自动转写",
            "fit": "纯 CPU，内存峰值约 300MB，docker run 挂两个目录即可",
        },
```

`edge-cases/promptforge` **故意残缺**，只给 pain，用来覆盖兜底分支：
```python
        tldr={"pain": "同一个 prompt 三个版本散落各处，改坏了不知道是哪次改的"},
```

`broken-json/llm-router` 是非 JSON 字符串，不动。

同时把 `MockLLMClient.complete` 的兜底回复也补上 tldr：

```python
        return _payload(
            one_liner="这是一个 mock 生成的兜底摘要",
            tldr={"pain": "mock 痛点", "solution": "mock 方案", "fit": "mock 部署说明"},
            highlights=["mock 亮点 A", "mock 亮点 B"],
            target_audience="本地测试",
            difficulty="medium",
            rating=3,
            rating_reason="mock 数据",
            detailed_intro="这是 mock 模式下的兜底介绍文本。",
            scores={"utility": 50, "problem_solving": 50, "popularity": 50, "nas_usability": 50},
        )
```

- [ ] **Step 7: 写假数据的断言测试**

追加到 `tests/test_pipeline.py` 的 `TestFullRun` 类中：

```python
    def test_winner_has_complete_tldr(self, mock_settings):
        s = run_once(mock_settings, report_date=REPORT_DATE)
        t = s.winner.analysis.tldr
        assert t.pain and t.solution and t.fit
        assert len(t.pain) <= 60

    def test_partial_tldr_candidate_falls_back(self, mock_settings):
        """promptforge 的假数据只给了 pain —— fit 应留空而非报错。"""
        run_once(mock_settings, report_date=REPORT_DATE)
        db = Database(mock_settings.db_path)
        rows = {r["repo_name"]: r for r in db.recent(10)}
        assert "edge-cases/promptforge" in rows
```

- [ ] **Step 8: 运行全部测试**

Run: `python -m pytest -q`
Expected: PASS

- [ ] **Step 9: 跑一次 mock 全链路目视确认**

Run: `python main.py --now --mock --data-dir ./data-local`
Expected: 退出码 0，日志无 `未分段` 告警

- [ ] **Step 10: 提交**

```bash
git add ai_analyzer.py mock_data.py tests/test_ai_analyzer.py tests/test_pipeline.py
git commit -m "feat: one_liner 与 tldr 的 Prompt 硬约束

one_liner 从'一句话核心价值'改成带正反示例的硬约束 —— 抽象地说
'不要名词堆叠'模型听不懂，必须把被否掉的那句原样贴给它看。
tldr 的 fit 明确要求写出依赖/内存/Docker/显卡等具体部署事实，
并点名禁止'适合自托管用户'这类无信息量表述。

假数据同步补 tldr，其中 promptforge 故意只给 pain 以覆盖兜底分支。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Hero 区重构为 B2 布局

**Files:**
- Modify: `report_generator.py:24-25`（评分环几何常量）
- Modify: `report_generator.py:95-135`（`build_context` 透出 tldr 行）
- Modify: `templates/report.html.j2`（CSS 与 Hero 区块）
- Test: `tests/test_report_and_notifier.py`

**Interfaces:**
- Consumes: Task 1 的 `Analysis.tldr`
- Produces: `report_generator.RING_BOX = 52`、`RING_STROKE = 4`、`RING_RADIUS = 24`、`RING_CIRCUMFERENCE = 150.8`
- Produces: `build_context()` 返回值新增键 `tldr_rows: list[dict[str, str]]`，每项形如 `{"label": "痛点", "text": "..."}`，**只包含非空行**

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_report_and_notifier.py`：

```python
from models import Tldr


class TestHeroB2:
    def _html(self, generator, project):
        return generator.render_html(project, REPORT_DATE)

    def test_one_liner_is_the_h1(self, generator, project):
        html = self._html(generator, project)
        h1 = html.split("<h1")[1].split("</h1>")[0]
        assert project.analysis.one_liner in h1
        assert project.repo.repo_name not in h1

    def test_repo_name_demoted_to_subtitle(self, generator, project):
        html = self._html(generator, project)
        assert project.repo.full_name in html
        assert "hero-sub" in html

    def test_tldr_three_rows_rendered(self, generator, project):
        project.analysis.tldr = Tldr(pain="痛点内容", solution="方案内容", fit="部署内容")
        html = self._html(generator, project)
        for text in ("痛点内容", "方案内容", "部署内容"):
            assert text in html
        assert html.count('class="tldr-row"') == 3

    def test_empty_tldr_row_is_hidden(self, generator, project):
        project.analysis.tldr = Tldr(pain="只有痛点", solution="", fit="")
        html = self._html(generator, project)
        assert html.count('class="tldr-row"') == 1
        assert "只有痛点" in html

    def test_tldr_section_absent_when_all_empty(self, generator, project):
        project.analysis.tldr = Tldr()
        html = self._html(generator, project)
        assert 'class="tldr-row"' not in html

    def test_tldr_labels_present(self, generator, project):
        project.analysis.tldr = Tldr(pain="a", solution="b", fit="c")
        html = self._html(generator, project)
        for label in ("痛点", "怎么解决", "我能用吗"):
            assert label in html

    def test_verdict_bar_carries_rating_and_difficulty(self, generator, project):
        html = self._html(generator, project)
        assert "verdict-bar" in html
        assert project.analysis.rating_reason in html
        assert "入门友好" in html or "需要折腾" in html or "硬核" in html

    def test_ring_geometry_matches_52px(self, generator, project):
        html = self._html(generator, project)
        assert 'viewBox="0 0 52 52"' in html
        assert 'cx="26"' in html

    def test_tldr_is_escaped(self, generator, project):
        project.analysis.tldr = Tldr(pain='<img src=x onerror="alert(1)">')
        html = self._html(generator, project)
        assert 'onerror="alert(1)"' not in html
        assert "&lt;img" in html

    def test_label_font_size_meets_nano_floor(self, generator, project):
        """DESIGN.md Nano 下限 11px —— 不得出现 9px/10px 字号。"""
        html = self._html(generator, project)
        assert "font-size: 9px" not in html
        assert "font-size: 10px" not in html
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_report_and_notifier.py::TestHeroB2 -v`
Expected: FAIL — `test_one_liner_is_the_h1` 报 `IndexError` 或断言失败（当前 h1 是仓库名）

- [ ] **Step 3: 改评分环几何常量**

`report_generator.py` 第 24 行：

```python
# 评分环由 80px 缩至 52px 并入判断条（见 2026-08-12 报告 IA 改造 spec）
RING_BOX = 52
RING_STROKE = 4
RING_RADIUS = (RING_BOX - RING_STROKE) // 2  # = 24
RING_CIRCUMFERENCE = round(2 * math.pi * RING_RADIUS, 2)  # = 150.8
```

- [ ] **Step 4: build_context 透出 tldr 行与环几何**

在 `build_context` 的 `return {` 之前插入：

```python
        tldr_rows = [
            {"label": label, "text": text}
            for label, text in (
                ("痛点", analysis.tldr.pain),
                ("怎么解决", analysis.tldr.solution),
                ("我能用吗", analysis.tldr.fit),
            )
            if text
        ]
```

在返回字典中加入四个键（放在 `ring_end` 之后）：

```python
            "tldr_rows": tldr_rows,
            "ring_box": RING_BOX,
            "ring_center": RING_BOX // 2,
            "ring_radius": RING_RADIUS,
            "ring_stroke": RING_STROKE,
```

- [ ] **Step 5: 替换模板 Hero 区块**

把 `templates/report.html.j2` 中从 `<!-- ============ Hero ============ -->` 到 `<!-- ============ 四维评分 ============ -->` 之前的整段，替换为：

```html
  <!-- ============ Hero (B2) ============ -->
  <div class="hero">
    <h1>{{ analysis.one_liner }}</h1>
    <div class="hero-sub">
      <span class="mono">{{ repo.full_name }}</span>
      <span class="sep">·</span>
      <span class="mono">★ {{ '{:,}'.format(repo.stars) }}</span>
      {% if repo.language %}
      <span class="sep">·</span>
      <span class="mono">{{ repo.language }}</span>
      {% endif %}
    </div>

    {% if tldr_rows %}
    <div class="tldr-card">
      {% for row in tldr_rows %}
      <div class="tldr-row">
        <div class="tldr-label">{{ row.label }}</div>
        <div class="tldr-text">{{ row.text }}</div>
      </div>
      {% endfor %}
    </div>
    {% endif %}

    <div class="verdict-bar">
      <div class="score-ring">
        <svg width="{{ ring_box }}" height="{{ ring_box }}"
             viewBox="0 0 {{ ring_box }} {{ ring_box }}" role="img"
             aria-label="综合评分 {{ total_score }} 分，满分 100 分">
          <defs>
            <linearGradient id="ringGrad" x1="0%" y1="0%" x2="100%" y2="0%">
              <stop offset="0%" stop-color="{{ ring_start }}"/>
              <stop offset="100%" stop-color="{{ ring_end }}"/>
            </linearGradient>
          </defs>
          <circle class="track" cx="{{ ring_center }}" cy="{{ ring_center }}"
                  r="{{ ring_radius }}" stroke-width="{{ ring_stroke }}"/>
          <circle class="progress" cx="{{ ring_center }}" cy="{{ ring_center }}"
                  r="{{ ring_radius }}" stroke="url(#ringGrad)"
                  stroke-width="{{ ring_stroke }}"
                  stroke-dasharray="{{ ring_circumference }}"
                  stroke-dashoffset="{{ ring_offset }}"/>
        </svg>
        <div class="value">{{ total_score_int }}</div>
      </div>
      <div class="verdict-text">
        <div class="verdict-head">
          <span class="verdict-stars">
            {%- for _ in range(analysis.rating) %}★{% endfor -%}
            {%- for _ in range(5 - analysis.rating) %}<span class="off">★</span>{% endfor -%}
          </span>
          <span class="badge {{ difficulty_class }}">{{ difficulty_label }}</span>
        </div>
        {% if analysis.rating_reason %}
        <p class="verdict-reason">{{ analysis.rating_reason }}</p>
        {% endif %}
      </div>
    </div>

    <a class="btn-primary hero-cta" href="{{ repo.html_url }}"
       target="_blank" rel="noopener noreferrer">前往 GitHub 仓库 ↗</a>
  </div>
```

- [ ] **Step 6: 替换 Hero 相关 CSS**

把模板 `<style>` 中从 `/* ---------------------------------------------------------- Hero */` 到 `/* ---------------------------------------------------------- 区块 */` 之前的整段，替换为：

```css
/* ---------------------------------------------------------- Hero (B2) */
.hero { margin-bottom: 48px; }
.hero h1 {
  font-size: 26px;
  font-weight: 700;
  line-height: 1.35;
  letter-spacing: -0.01em;
  margin: 0 0 10px;
  color: var(--color-text-primary);
}
.hero-sub {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px 10px;
  font-size: 12px;
  color: var(--color-text-tertiary);
  margin-bottom: 20px;
}
.hero-sub .mono { font-family: var(--font-mono); }
.hero-sub .sep { color: var(--color-text-tertiary); }

.tldr-card {
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 4px 16px;
  box-shadow: var(--shadow-md);
}
.tldr-row {
  display: flex;
  gap: 12px;
  padding: 13px 0;
  border-bottom: 1px solid var(--color-border-subtle);
}
.tldr-row:last-child { border-bottom: none; }
.tldr-label {
  flex: 0 0 60px;
  font-size: 11px;
  font-weight: 600;
  letter-spacing: 0.02em;
  line-height: 1.7;
  color: var(--color-accent);
}
.tldr-text {
  font-size: 14px;
  line-height: 1.55;
  color: var(--color-text-primary);
}

.verdict-bar {
  display: flex;
  align-items: center;
  gap: 14px;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 12px;
  padding: 16px;
  margin-top: 12px;
  box-shadow: var(--shadow-md);
}
.score-ring { position: relative; width: 52px; height: 52px; flex: 0 0 52px; }
.score-ring svg { transform: rotate(-90deg); display: block; }
.score-ring .track { fill: none; stroke: var(--color-border); }
.score-ring .progress {
  fill: none;
  stroke-linecap: round;
  transition: stroke-dashoffset 0.6s cubic-bezier(0.25, 0.46, 0.45, 0.94);
}
.score-ring .value {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-family: var(--font-mono);
  font-size: 17px;
  font-weight: 700;
  color: var(--color-text-primary);
}
.verdict-text { min-width: 0; }
.verdict-head {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 5px;
  flex-wrap: wrap;
}
.verdict-stars { color: var(--color-warning); letter-spacing: 1px; font-size: 14px; }
.verdict-stars .off { color: var(--color-text-tertiary); }
.verdict-reason {
  margin: 0;
  font-size: 13px;
  line-height: 1.55;
  color: var(--color-text-secondary);
}
.hero-cta { display: flex; width: 100%; margin-top: 12px; }
```

在移动端媒体查询 `@media (max-width: 640px)` 内，把原来的 `.hero h1 { font-size: 28px; }` 等规则替换为：

```css
  .hero h1 { font-size: 21px; }
  .tldr-label { flex: 0 0 52px; }
  .tldr-text { font-size: 13px; }
```

并删除该媒体查询里已不存在的 `.one-liner`、`.score-ring { width: 64px; height: 64px; }`、`.score-ring .value { font-size: 20px; }` 三条规则。

- [ ] **Step 7: 运行测试确认通过**

Run: `python -m pytest tests/test_report_and_notifier.py::TestHeroB2 -v`
Expected: PASS，10 个用例全绿

- [ ] **Step 8: 运行全部测试**

Run: `python -m pytest -q`
Expected: PASS。若 `TestHtmlReport::test_contains_key_content` 因断言仓库名在 h1 而失败，把该断言改为 `assert project.repo.repo_name in html`（仍出现在副标题与仓库信息表中）。

- [ ] **Step 9: 提交**

```bash
git add report_generator.py templates/report.html.j2 tests/test_report_and_notifier.py
git commit -m "feat: Hero 区重构为 B2 布局

一句话价值升为 h1，仓库名降为等宽副标题 —— 原来 36px 的位置给了
一个对陌生项目零信息量的仓库名，而'这是什么'只有 22px 且被评分环
压住。tldr 三行带标签卡接管首屏，空行整行不渲染。评分环从 80px
缩到 52px 并入判断条，标签字号 11px 守住 Nano 下限。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: 区块重排与评分区压扁

**Files:**
- Modify: `templates/report.html.j2`（区块顺序、评分区标记与 CSS、删除旧「快速上手」区块）
- Test: `tests/test_report_and_notifier.py`

**Interfaces:**
- Consumes: Task 3 的 Hero 结构与 `dimensions` 上下文（已存在，含 `label` / `score` / `weight` / `css_class`）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_report_and_notifier.py`：

```python
class TestSectionOrder:
    SECTIONS = ["技术亮点", "详细介绍", "评分依据", "仓库信息"]

    def test_sections_appear_in_reading_order(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        positions = [html.index(f'id="sec-{k}"') for k in
                     ("highlights", "intro", "scores", "meta")]
        assert positions == sorted(positions), "区块顺序必须是 亮点→正文→评分→仓库"

    def test_score_section_titled_评分依据(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        assert "评分依据" in html
        assert "评分详情" not in html

    def test_scores_use_compact_rows_not_grid(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        assert html.count('class="score-line"') == 4
        assert "score-grid" not in html
        assert "score-card" not in html

    def test_score_lines_carry_weight_and_value(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        for token in ("35%", "30%", "25%", "10%"):
            assert token in html
        assert "width: 90%" in html  # utility=90 in the fixture

    def test_target_audience_moved_into_highlights(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        highlights_block = html.split('id="sec-highlights"')[1].split('id="sec-intro"')[0]
        assert project.analysis.target_audience in highlights_block

    def test_old_quickstart_section_removed(self, generator, project):
        html = generator.render_html(project, REPORT_DATE)
        assert "快速上手" not in html
        assert "verdict-grid" not in html

    def test_duplicate_cta_removed(self, generator, project):
        """CTA 已进 Hero，底部不再重复。"""
        html = generator.render_html(project, REPORT_DATE)
        assert html.count("前往 GitHub 仓库 ↗") == 1

    def test_no_dead_css_left_behind(self, generator, project):
        """失效选择器必须删干净 —— 旧 .verdict-reason 位置靠后会覆盖 Hero 的新定义。"""
        html = generator.render_html(project, REPORT_DATE)
        for selector in (".verdict-grid", ".verdict-item", ".cta-row",
                         ".btn-ghost", ".score-bar", ".score-card"):
            assert selector not in html, f"残留失效 CSS: {selector}"
        assert html.count(".verdict-reason") == 1
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python -m pytest tests/test_report_and_notifier.py::TestSectionOrder -v`
Expected: FAIL — `ValueError: substring not found`（`id="sec-highlights"` 尚未定义）

- [ ] **Step 3: 重排区块并改写评分区**

把模板中从 `<!-- ============ 四维评分 ============ -->` 到 `<!-- ============ CTA ============ -->` 之前的全部内容，替换为下面四个区块（顺序即最终顺序）：

```html
  <!-- ============ 技术亮点 ============ -->
  {% if analysis.highlights or analysis.target_audience %}
  <section aria-labelledby="sec-highlights">
    <h2 class="section-title" id="sec-highlights">技术亮点</h2>
    <div class="card">
      {% if analysis.highlights %}
      <ul class="highlight-list">
        {% for item in analysis.highlights %}
        <li>{{ item }}</li>
        {% endfor %}
      </ul>
      {% endif %}
      {% if analysis.target_audience %}
      <p class="audience-note">
        <strong>适合谁：</strong>{{ analysis.target_audience }}
      </p>
      {% endif %}
    </div>
  </section>
  {% endif %}

  <!-- ============ 详细介绍 ============ -->
  {% if intro_html %}
  <section aria-labelledby="sec-intro">
    <h2 class="section-title" id="sec-intro">详细介绍</h2>
    <div class="card">
      <div class="prose">{{ intro_html|safe }}</div>
    </div>
  </section>
  {% endif %}

  <!-- ============ 评分依据 ============ -->
  <section aria-labelledby="sec-scores">
    <h2 class="section-title" id="sec-scores">评分依据</h2>
    <div class="card">
      {% for dim in dimensions %}
      <div class="score-line">
        <span class="score-line-label">{{ dim.label }}<span class="w">{{ dim.weight }}</span></span>
        <span class="score-line-bar" role="img" aria-label="{{ dim.label }} {{ dim.score }} 分">
          <span class="score-line-fill {{ dim.css_class }}" style="width: {{ dim.score }}%"></span>
        </span>
        <span class="score-line-value">{{ dim.score }}</span>
      </div>
      {% endfor %}
      <p class="score-note">总分 = 各维度得分 × 权重之和 = <strong>{{ total_score }}</strong> / 100</p>
    </div>
  </section>

  <!-- ============ 仓库信息 ============ -->
  <section aria-labelledby="sec-meta">
    <h2 class="section-title" id="sec-meta">仓库信息</h2>
    <div class="card">
      {% if repo.topics %}
      <div class="badge-row">
        {% for topic in repo.topics[:8] %}
        <span class="badge badge-topic">{{ topic }}</span>
        {% endfor %}
      </div>
      {% endif %}
      <table class="meta-table">
        <tbody>
          <tr><th>仓库</th><td>{{ repo.full_name }}</td></tr>
          <tr><th>Star</th><td>{{ '{:,}'.format(repo.stars) }}</td></tr>
          <tr><th>Fork</th><td>{{ '{:,}'.format(repo.forks) }}</td></tr>
          <tr><th>Open Issues</th><td>{{ '{:,}'.format(repo.open_issues) }}</td></tr>
          <tr><th>主语言</th><td>{{ repo.language or '—' }}</td></tr>
          <tr><th>创建时间</th><td>{{ created_display }}</td></tr>
          {% if repo.description %}
          <tr><th>官方描述</th><td style="font-family: var(--font-sans); text-align: left;">{{ repo.description }}</td></tr>
          {% endif %}
        </tbody>
      </table>
    </div>
  </section>
```

然后**删除**模板中原有的 `<!-- ============ CTA ============ -->` 整个 `.cta-row` 块（CTA 已并入 Hero），保留 `<footer>` 不变。

- [ ] **Step 4: 替换评分区 CSS**

把模板 `<style>` 中 `.score-grid` / `.score-card` / `.score-card-head` / `.score-card-label` / `.score-card-weight` / `.score-card-value` 六条规则整段删除，替换为：

```css
/* ---------------------------------------------------------- 评分细条 */
.score-line {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 9px 0;
}
.score-line + .score-line { border-top: 1px solid var(--color-border-subtle); }
.score-line-label {
  flex: 0 0 108px;
  font-size: 13px;
  color: var(--color-text-secondary);
  display: flex;
  justify-content: space-between;
  gap: 6px;
}
.score-line-label .w { font-family: var(--font-mono); font-size: 11px; color: var(--color-text-tertiary); }
.score-line-bar {
  flex: 1 1 auto;
  height: 6px;
  border-radius: 3px;
  background: var(--color-border);
  overflow: hidden;
}
.score-line-fill {
  display: block;
  height: 100%;
  border-radius: 3px;
  transition: width 0.6s cubic-bezier(0.25, 0.46, 0.45, 0.94);
}
.score-line-value {
  flex: 0 0 30px;
  text-align: right;
  font-family: var(--font-mono);
  font-size: 14px;
  font-weight: 600;
  color: var(--color-text-primary);
}
.score-note {
  margin: 14px 0 0;
  padding-top: 12px;
  border-top: 1px solid var(--color-border-subtle);
  font-size: 12px;
  color: var(--color-text-tertiary);
}
.score-note strong { font-family: var(--font-mono); color: var(--color-text-secondary); }
.audience-note {
  margin: 16px 0 0;
  padding-top: 14px;
  border-top: 1px solid var(--color-border-subtle);
  font-size: 14px;
  line-height: 1.6;
  color: var(--color-text-body);
}
.audience-note strong { color: var(--color-text-primary); }
```

保留 `.score-high` / `.score-mid` / `.score-low` 三条渐变规则不动 —— 它们现在作用于 `.score-line-fill`。

**同时删除以下已失效的 CSS 规则**（Task 3 与本任务移除了它们的全部使用点，留着就是死代码；其中 `.verdict-reason` 尤其危险——旧定义在样式表中位置靠后，会覆盖 Task 3 在 Hero 里新定义的同名规则）：

- `.verdict-grid`
- `.verdict-item`、`.verdict-item .k`、`.verdict-item .v`
- `.stars`、`.stars .off`（已由 `.verdict-stars` 取代）
- `.verdict-reason`（旧的 15px 版本，Task 3 已在 Hero CSS 中重新定义为 13px）
- `.cta-row`
- `.btn-ghost`、`.btn-ghost:hover`（底部 CTA 行删除后无使用点）
- `.score-bar`、`.score-bar-fill`（已由 `.score-line-bar` / `.score-line-fill` 取代）

删除后用 `grep -n "verdict-grid\|verdict-item\|btn-ghost\|cta-row\|score-bar\|\.stars" templates/report.html.j2` 确认无残留。

在移动端媒体查询里，把 `.score-grid { grid-template-columns: 1fr; }` 和 `.verdict-grid { grid-template-columns: 1fr; }` 两条替换为：

```css
  .score-line-label { flex: 0 0 92px; font-size: 12px; }
```

并删除 `.cta-row .btn-primary, .cta-row .btn-ghost { flex: 1 1 100%; }` 一条。

- [ ] **Step 5: 运行测试确认通过**

Run: `python -m pytest tests/test_report_and_notifier.py::TestSectionOrder -v`
Expected: PASS，7 个用例全绿

- [ ] **Step 6: 运行全部测试**

Run: `python -m pytest -q`
Expected: PASS。`TestHtmlReport::test_score_bars_and_ring` 里断言的 `score-high` / `score-mid` 类名仍然成立；若它断言了 `width: 90%` 之外的旧结构，按新标记调整。

- [ ] **Step 7: 提交**

```bash
git add templates/report.html.j2 tests/test_report_and_notifier.py
git commit -m "feat: 区块按阅读成本重排，评分区压扁为四行细条

顺序改为 技术亮点 → 详细介绍 → 评分依据 → 仓库信息，按读者愿意
付出的时间递增排：30 秒扫亮点、3 分钟读正文、存疑才查评分。原来
最可扫描的技术亮点在 Y=1763，要滚两屏才看到。

评分区从 2×2 卡片网格（309px）压成四行细条（约 90px）—— 总分与
结论已在首屏交代完，此处只剩'给你看我怎么算的'这一个作用。四行
左对齐成扫描线，权重与分数比网格更好比较。

原「快速上手」区块并入 Hero 判断条，「适合谁」移入技术亮点，
底部重复的 CTA 删除。

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: DESIGN.md 同步与真机渲染验证

**Files:**
- Modify: `DESIGN.md`（Type Scale 表、§4 Score Ring、§4 新增 tldr 与评分细条条目、§9 Quick Reference）
- Test: 浏览器实测（无新增单测文件）

**Interfaces:**
- Consumes: Task 3、Task 4 的最终模板产物

- [ ] **Step 1: 生成一份真实报告**

```bash
python main.py --now --mock --data-dir ./data-local
```

Expected: 退出码 0，`data-local/reports/` 下出现新的 HTML

- [ ] **Step 2: 启动本地服务**

```bash
python main.py --serve --data-dir ./data-local --port 8899
```

Expected: 日志出现 `报告 HTTP 服务已启动`

- [ ] **Step 3: 移动端实测（375px）**

在浏览器打开 `http://127.0.0.1:8899/reports/`，选最新报告，把视口设为 375×812，在控制台执行：

```javascript
JSON.stringify({
  hScroll: document.documentElement.scrollWidth > window.innerWidth,
  h1: getComputedStyle(document.querySelector('.hero h1')).fontSize,
  tldrRows: document.querySelectorAll('.tldr-row').length,
  tldrLabel: getComputedStyle(document.querySelector('.tldr-label')).fontSize,
  tldrText: getComputedStyle(document.querySelector('.tldr-text')).fontSize,
  ring: getComputedStyle(document.querySelector('.score-ring')).width,
  ctaH: document.querySelector('.hero-cta').getBoundingClientRect().height,
  scoreLines: document.querySelectorAll('.score-line').length,
  firstScreen: [...document.querySelectorAll('.hero, section')]
    .filter(e => e.getBoundingClientRect().top + scrollY < 812)
    .map(e => e.querySelector('.section-title')?.textContent.trim() || 'HERO'),
  minFont: Math.min(...[...document.querySelectorAll('*')]
    .map(e => parseFloat(getComputedStyle(e).fontSize)).filter(Boolean)),
  externalReqs: performance.getEntriesByType('resource').map(r => r.name)
})
```

Expected 全部满足：
- `hScroll` 为 `false`
- `h1` 为 `21px`
- `tldrRows` 为 `3`
- `tldrLabel` 为 `11px`，`tldrText` 为 `13px`
- `ring` 为 `52px`
- `ctaH` ≥ `44`
- `scoreLines` 为 `4`
- `firstScreen` 包含 `HERO`（首屏能装下大标题、tldr 三行、判断条）
- `minFont` ≥ `11`
- `externalReqs` 为 `[]`

任一项不达标则回到 Task 3 或 Task 4 修正后重测。

- [ ] **Step 4: 桌面端实测（1280px）**

重新加载页面（改视口后必须整页重载，否则媒体查询不重算），执行同一段脚本。

Expected: `h1` 为 `26px`，`tldrText` 为 `14px`，`hScroll` 为 `false`，其余同上。

- [ ] **Step 5: 降级态实测**

```bash
python -c "import json,pathlib,datetime; \
from models import Repo, AnalyzedProject; \
from ai_analyzer import build_degraded_analysis; \
from report_generator import ReportGenerator; \
r=Repo(full_name='a/degraded-demo',html_url='https://github.com/a/b',description='测试降级',language='Go',stars=12); \
p=AnalyzedProject(repo=r,analysis=build_degraded_analysis(r,'LLM 超时')); \
g=ReportGenerator(pathlib.Path('data-local/reports'),pathlib.Path('data-local/archive'),report_base_url='',model_name='x'); \
print(g.write_report(p,datetime.date.today())[0])"
```

Expected: 生成的页面顶部有橙色虚线降级横幅，tldr 三行齐全且 `fit` 显示"AI 分析不可用，部署要求请查看仓库 README"

- [ ] **Step 6: 更新 DESIGN.md 的 Type Scale**

把 §3 Type Scale 表中 Display 与 H1 两行替换为：

```
| Hero Title | 26px / 1.625rem | 700 | 1.35 | -0.01em | 报告页 Hero 一句话价值（内容是句子，不是项目名） |
| H1 | 28px / 1.75rem | 600 | 1.25 | -0.015em | 页面主标题（报告页不使用） |
```

在表格下方加一段：

```
> **2026-08-12 修订**：报告页 Hero 标题不再是项目名，而是一句话价值。
> 36px 的 Display 放长句在 375px 屏上会占掉近半屏，故新增 Hero Title 层级，
> 移动端降至 21px。所有字号一律不得低于 Nano 的 11px。
```

- [ ] **Step 7: 更新 DESIGN.md 的 §4 组件样式**

把 §4 的 `### Score Ring（评分环形指示器）` 小节的 CSS 块替换为：

```css
.score-ring {
  width: 52px;          /* 2026-08-12 修订：由 80px 缩小并入判断条 */
  height: 52px;
  flex: 0 0 52px;
  position: relative;
}
.score-ring .value {
  font-family: 'JetBrains Mono', monospace;
  font-size: 17px;
  font-weight: 700;
  color: #FAFAFA;
}
```

在该小节后新增两个小节：

```markdown
### TL;DR 三要素卡（Hero）

```css
.tldr-row {
  display: flex;
  gap: 12px;
  padding: 13px 0;
  border-bottom: 1px solid #1E1E21;
}
.tldr-label {
  flex: 0 0 60px;       /* 移动端 52px */
  font-size: 11px;      /* Nano 下限，不得再小 */
  font-weight: 600;
  color: #3B82F6;
}
.tldr-text { font-size: 14px; line-height: 1.55; color: #FAFAFA; }
```

三行分别回答「痛点 / 怎么解决 / 我能用吗」。任一行内容为空时**整行不渲染**，
不留空壳。

### 评分细条（替代原 2×2 评分卡片网格）

```css
.score-line { display: flex; align-items: center; gap: 12px; padding: 9px 0; }
.score-line-label { flex: 0 0 108px; font-size: 13px; }  /* 移动端 92px */
.score-line-bar { flex: 1 1 auto; height: 6px; border-radius: 3px; background: #27272A; }
.score-line-value { flex: 0 0 30px; text-align: right; font-family: monospace; }
```

评分在新结构中是**佐证而非主角**——总分与结论已在 Hero 交代完毕，
此区块只回答"这个分怎么算出来的"，因此不再占用整屏。
```

- [ ] **Step 8: 更新 §9 Quick Reference**

把 §9 的 Quick Reference 引用段替换为：

```
> 暗色主题开发者仪表盘风格。背景 `#0A0A0B`，卡片 `#111113`，边框 `#27272A`。
> 无衬线 `Inter` + 等宽 `JetBrains Mono`。**报告页 Hero 的大标题是一句话价值
> 而非项目名**（桌面 26px / 移动 21px / 700），下接「痛点 / 怎么解决 / 我能用吗」
> 三行标签卡，再接评分环 52px 的判断条。首屏以下顺序固定为
> 技术亮点 → 详细介绍 → 评分依据 → 仓库信息。评分用四行横向细条而非卡片网格。
> 卡片圆角 `12px`。单列布局，移动端优先，字号不低于 11px。
> 所有 HTML 自包含，无外部依赖，无 JS。
```

- [ ] **Step 9: 运行全部测试并停掉服务**

```bash
python -m pytest -q
```

Expected: PASS，全部通过

- [ ] **Step 10: 提交**

```bash
git add DESIGN.md
git commit -m "docs: DESIGN.md 同步报告页 IA 改造的三处偏离

- 新增 Hero Title 层级（桌面 26px / 移动 21px），Display 36px 不再
  用于报告页 —— 标题内容从项目名换成一句话价值，长句放 36px 在
  375px 屏上会占掉近半屏
- Score Ring 由 80px 缩至 52px 并入判断条
- 评分卡片 2×2 网格改为四行横向细条
- 新增 TL;DR 三要素卡规范，明确空行整行不渲染
- 全局补充：字号不得低于 Nano 的 11px

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## 完成标准

全部任务完成后应满足：

- `python -m pytest -q` 全绿（预计 152 → 约 186 个用例）
- `python main.py --now --mock` 退出码 0，日志无 `未分段` 告警
- 375px 视口下：无横向滚动、h1 为 21px、tldr 三行、最小字号 ≥ 11px、CTA ≥ 44px
- 1280px 视口下：h1 为 26px、无横向滚动
- 报告页外部资源请求数为 `0`
- 降级态页面 tldr 三行齐全且不编造部署结论
- `DESIGN.md` 与实际实现一致，三处偏离均已记录
