# DESIGN.md — GitHub AI Insight

> 设计系统规范 v1.0 | NAS 自托管 AI 日报系统
> 风格参考: **Vercel** (极简黑白) + **Raycast** (暗色渐变) + **Linear** (精准数据排版)

---

## 1. Visual Theme & Atmosphere（视觉主题与氛围）

**设计哲学**: 数据驱动的暗色仪表盘美学。信息密度优先，但不拥挤。用微妙的渐变和精确的阴影层级区分内容区块，而非硬边框。整体感觉：安静、专业、值得信赖——像凌晨两点还在默默运转的服务器。

**视觉基调**: 深色极简主义 + 技术精密感 + 克制的色彩强调

**核心特征关键词**: `暗色优先` `数据密集` `微光渐变` `等宽点缀` `卡片层级`

**光影与质感**:
- 纯扁平为基础，仅在卡片浮层使用微阴影（不超过 2 层）
- 无毛玻璃效果（自包含 HTML 需兼容低性能 NAS 设备）
- 渐变仅用于评分进度条和 Logo 标识，不用于背景

---

## 2. Color Palette & Roles（调色板与角色）

### Primary Colors

| 角色 | HEX | CSS 变量 | 使用场景 |
|------|-----|----------|----------|
| Background | `#0A0A0B` | `--color-bg` | 页面主背景 |
| Surface | `#111113` | `--color-surface` | 卡片、内容区块背景 |
| Surface Elevated | `#18181B` | `--color-surface-elevated` | 悬浮卡片、模态框 |
| Text Primary | `#FAFAFA` | `--color-text-primary` | 标题、正文主色 |
| Text Secondary | `#A1A1AA` | `--color-text-secondary` | 副标题、描述、时间戳 |
| Text Tertiary | `#52525B` | `--color-text-tertiary` | 占位符、禁用态、分割线 |

### Accent / Interactive

| 角色 | HEX | CSS 变量 | 使用场景 |
|------|-----|----------|----------|
| Accent Blue | `#3B82F6` | `--color-accent` | 链接、主要交互元素 |
| Accent Hover | `#60A5FA` | `--color-accent-hover` | 链接 hover 态 |
| Accent Subtle | `rgba(59,130,246,0.12)` | `--color-accent-subtle` | 选中态背景、Tag 背景 |

### Score Gradient（评分渐变）

| 分数段 | 起始色 | 终止色 | CSS 变量 |
|--------|--------|--------|----------|
| 高 (80-100) | `#22C55E` | `#4ADE80` | `--score-high-start` / `--score-high-end` |
| 中 (50-79) | `#F59E0B` | `#FBBF24` | `--score-mid-start` / `--score-mid-end` |
| 低 (0-49) | `#EF4444` | `#F87171` | `--score-low-start` / `--score-low-end` |

### Semantic Colors

| 角色 | HEX | CSS 变量 | 使用场景 |
|------|-----|----------|----------|
| Success | `#22C55E` | `--color-success` | 推送成功、低难度标签 |
| Warning | `#F59E0B` | `--color-warning` | API 降级、中难度标签 |
| Error | `#EF4444` | `--color-error` | 推送失败、错误状态 |
| Info | `#3B82F6` | `--color-info` | 信息提示 |

### Border & Divider

| 角色 | HEX | CSS 变量 |
|------|-----|----------|
| Border Default | `#27272A` | `--color-border` |
| Border Subtle | `#1E1E21` | `--color-border-subtle` |

---

## 3. Typography Rules（排版规则）

### Font Family

```css
--font-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', Arial, sans-serif;
--font-mono: 'JetBrains Mono', 'SF Mono', 'Fira Code', 'Cascadia Code', Consolas, monospace;
```

**设计哲学**: 无衬线体负责阅读舒适性，等宽字体用于代码片段、仓库名、数值数据——强化"开发者工具"气质。

### Type Scale

| 层级 | Font Size | Weight | Line Height | Letter Spacing | 使用场景 |
|------|-----------|--------|-------------|----------------|----------|
| Hero Title | 26px / 1.625rem | 700 | 1.35 | -0.01em | 报告页 Hero 一句话价值（内容是句子，不是项目名） |
| H1 | 28px / 1.75rem | 600 | 1.25 | -0.015em | 页面主标题（报告页不使用） |
| H2 | 22px / 1.375rem | 600 | 1.3 | -0.01em | 区块标题 |
| H3 | 18px / 1.125rem | 600 | 1.4 | -0.005em | 卡片标题、项目名称 |
| Body Large | 16px / 1rem | 400 | 1.6 | 0 | 报告正文、详细介绍 |
| Body | 14px / 0.875rem | 400 | 1.5 | 0 | 正文默认、描述文字 |
| Body Mono | 14px / 0.875rem | 400 | 1.5 | 0 | 仓库名、路径 |
| Caption | 12px / 0.75rem | 400 | 1.5 | 0.01em | 时间戳、辅助信息 |
| Nano | 11px / 0.6875rem | 500 | 1.4 | 0.02em | 标签、Badge 文字 |

> **2026-08-12 修订**：报告页 Hero 标题不再是项目名，而是一句话价值。
> 36px 的 Display 放长句在 375px 屏上会占掉近半屏，故新增 Hero Title 层级，
> 移动端降至 21px。所有字号一律不得低于 Nano 的 11px。

---

## 4. Component Stylings（组件样式）

### Buttons

```css
/* Primary Button */
.btn-primary {
  background: #3B82F6;
  color: #FFFFFF;
  border: none;
  border-radius: 8px;
  padding: 10px 20px;
  font-size: 14px;
  font-weight: 500;
  line-height: 1;
  cursor: pointer;
  transition: background 0.15s ease;
}
.btn-primary:hover { background: #2563EB; }
.btn-primary:active { background: #1D4ED8; }

/* Ghost Button */
.btn-ghost {
  background: transparent;
  color: #A1A1AA;
  border: 1px solid #27272A;
  border-radius: 8px;
  padding: 10px 20px;
  font-size: 14px;
  font-weight: 500;
  transition: all 0.15s ease;
}
.btn-ghost:hover {
  background: #18181B;
  color: #FAFAFA;
  border-color: #3F3F46;
}

/* Link Button */
.btn-link {
  background: none;
  border: none;
  color: #3B82F6;
  font-size: 14px;
  font-weight: 500;
  padding: 0;
  cursor: pointer;
  text-decoration: none;
}
.btn-link:hover { color: #60A5FA; text-decoration: underline; }
```

### Cards

```css
.card {
  background: #111113;
  border: 1px solid #27272A;
  border-radius: 12px;
  padding: 24px;
  box-shadow:
    0 1px 2px rgba(0,0,0,0.3),
    0 4px 12px rgba(0,0,0,0.15);
}

.card-elevated {
  background: #18181B;
  border: 1px solid #3F3F46;
  border-radius: 12px;
  padding: 24px;
  box-shadow:
    0 2px 4px rgba(0,0,0,0.4),
    0 8px 24px rgba(0,0,0,0.25);
}
```

### Score Bars（评分进度条）

```css
.score-bar {
  height: 6px;
  border-radius: 3px;
  background: #27272A;
  overflow: hidden;
}
.score-bar-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.6s cubic-bezier(0.25, 0.46, 0.45, 0.94);
}
/* 根据分数段应用渐变 */
.score-high { background: linear-gradient(90deg, #22C55E, #4ADE80); }
.score-mid  { background: linear-gradient(90deg, #F59E0B, #FBBF24); }
.score-low  { background: linear-gradient(90deg, #EF4444, #F87171); }
```

### Badges / Tags

```css
.badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: 9999px;
  font-size: 11px;
  font-weight: 500;
  letter-spacing: 0.02em;
  line-height: 1.6;
}
.badge-difficulty-low  { background: rgba(34,197,94,0.15); color: #4ADE80; }
.badge-difficulty-mid  { background: rgba(245,158,11,0.15); color: #FBBF24; }
.badge-difficulty-high { background: rgba(239,68,68,0.15); color: #F87171; }
.badge-topic           { background: #18181B; color: #A1A1AA; border: 1px solid #27272A; }
```

### Score Ring（评分环形指示器）

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

### Markdown Content Area（报告正文区）

```css
.prose {
  color: #FAFAFA;
  font-size: 16px;
  line-height: 1.7;
}
.prose h2 {
  font-size: 22px;
  font-weight: 600;
  margin: 32px 0 16px;
  color: #FAFAFA;
}
.prose p {
  margin: 0 0 16px;
  color: #D4D4D8;
}
.prose code {
  font-family: 'JetBrains Mono', monospace;
  font-size: 13px;
  background: #18181B;
  border: 1px solid #27272A;
  border-radius: 4px;
  padding: 2px 6px;
  color: #A78BFA;
}
.prose ul { padding-left: 24px; margin: 0 0 16px; }
.prose li { margin-bottom: 6px; color: #D4D4D8; }
.prose a { color: #3B82F6; text-decoration: none; }
.prose a:hover { text-decoration: underline; }
```

---

## 5. Layout Principles（布局原则）

### Spacing System

基数: **4px**，使用 4 的倍数

| Token | 值 | 使用场景 |
|-------|----|----------|
| `space-1` | 4px | 图标与文字间距 |
| `space-2` | 8px | 紧凑元素间距 |
| `space-3` | 12px | Badge 内边距 |
| `space-4` | 16px | 卡片内元素间距 |
| `space-5` | 20px | 关联区块间距 |
| `space-6` | 24px | 卡片内边距 |
| `space-8` | 32px | 区块间距 |
| `space-10` | 40px | 大区块间距 |
| `space-12` | 48px | 页面级区块间距 |
| `space-16` | 64px | Hero 区域上下留白 |

### Container & Grid

```css
--container-max: 800px;      /* 报告页最大宽度 */
--container-wide-max: 1120px; /* 仪表板最大宽度 */
--container-padding: 24px;    /* 移动端内边距 */
```

- 报告页使用单列布局，`max-width: 800px` 居中
- 无需多列网格（单页报告以线性阅读为主）

### 留白哲学

克制但明确。每个区块通过 `48-64px` 的垂直间距分隔，卡片内部保持 `24px` 呼吸空间。内容密度高但不拥挤——关键是在**数据区块之间**留出清晰的视觉停顿。

---

## 6. Depth & Elevation（深度与层级）

### Shadow System

| 层级 | CSS 值 | 使用场景 |
|------|--------|----------|
| `shadow-sm` | `0 1px 2px rgba(0,0,0,0.3)` | 输入框、小按钮 |
| `shadow-md` | `0 1px 2px rgba(0,0,0,0.3), 0 4px 12px rgba(0,0,0,0.15)` | 默认卡片 |
| `shadow-glow` | `0 0 20px rgba(59,130,246,0.15)` | 焦点元素、活跃状态 |

### Surface Layers

| 层级 | 背景色 | 用途 |
|------|--------|------|
| Layer 0 - Background | `#0A0A0B` | 页面底色 |
| Layer 1 - Surface | `#111113` | 内容卡片 |
| Layer 2 - Elevated | `#18181B` | 代码块、引用块 |
| Layer 3 - Overlay | `rgba(0,0,0,0.6)` | 模态遮罩（如有） |

### Z-index Scale

| 值 | 用途 |
|----|------|
| 0 | 默认层 |
| 10 | 卡片浮层 |
| 50 | 固定导航 |
| 100 | 模态框、Toast |

---

## 7. Do's and Don'ts（设计规范与禁忌）

### Do's

- ✅ **使用等宽字体展示仓库名和数值数据**——强化开发者工具属性
- ✅ **评分使用颜色编码**——绿/黄/红三色直觉传达高低
- ✅ **卡片之间保持 48px+ 间距**——暗色背景下留白即是分隔
- ✅ **链接使用蓝色 + 下划线 hover**——可识别性优先于极简
- ✅ **HTML 报告自包含**——不依赖外部 CDN，离线可正常浏览
- ✅ **移动端优先**——企微内打开报告页主要在手机上阅读
- ✅ **数据使用表格或进度条对齐**——便于快速扫描比较

### Don'ts

- ❌ **不要在暗色背景上使用纯白 `#FFFFFF` 大面积填充**——用 `#FAFAFA` 替代
- ❌ **不要使用超过 2 种渐变色**——渐变仅用于评分条和总分环
- ❌ **不要在小屏上使用多列布局**——报告页始终单列
- ❌ **不要在正文中使用等宽字体**——等宽仅限代码和仓库名
- ❌ **不要使用 box-shadow 模拟边框**——边框用 `border`，阴影只表达层级
- ❌ **不要使用动画过渡超过 300ms**——工具类产品应感觉干脆利落
- ❌ **不要在推送消息中堆砌所有数据**——企微消息只放核心摘要，详情引流至 HTML

---

## 8. Responsive Behavior（响应式行为）

### Breakpoints

| 断点 | 宽度 | 说明 |
|------|------|------|
| Mobile | `< 640px` | 企微内置浏览器、手机 Safari |
| Tablet | `640px - 1024px` | 平板横屏、小窗口 |
| Desktop | `> 1024px` | 桌面浏览器 |

### Mobile 策略 (`< 640px`)

- 容器 padding 从 `24px` 缩至 `16px`
- Display 字号从 `36px` 降至 `28px`
- H1 字号从 `28px` 降至 `22px`
- 评分卡片从横向排列改为纵向堆叠
- 评分环形指示器缩小至 `64px`
- 触摸目标最小尺寸: `44x44px`
- 代码块启用水平滚动

### Tablet 策略 (`640px - 1024px`)

- 容器 `max-width: 800px` 居中
- 评分卡片保持横向但缩小间距
- 两列布局可用（如评分概览 + 详情并排）

### Font Scaling

| 层级 | Mobile | Tablet+ |
|------|--------|---------|
| Display | 28px | 36px |
| H1 | 22px | 28px |
| H2 | 18px | 22px |
| Body | 14px | 16px |

---

## 9. Agent Prompt Guide（AI 代理提示指南）

### Quick Reference

> 暗色主题开发者仪表盘风格。背景 `#0A0A0B`，卡片 `#111113`，边框 `#27272A`。
> 无衬线 `Inter` + 等宽 `JetBrains Mono`。**报告页 Hero 的大标题是一句话价值
> 而非项目名**（桌面 26px / 移动 21px / 700），下接「痛点 / 怎么解决 / 我能用吗」
> 三行标签卡，再接评分环 52px 的判断条。首屏以下顺序固定为
> 技术亮点 → 详细介绍 → 评分依据 → 仓库信息。评分用四行横向细条而非卡片网格。
> 卡片圆角 `12px`。单列布局，移动端优先，字号不低于 11px。
> 所有 HTML 自包含，无外部依赖，无 JS。

### Component Prompts

**Prompt 1 — 项目报告 Hero 区块**:
```
创建一个深色主题的报告页 Hero 区块。背景 #0A0A0B，居中布局。项目名称使用 36px/700 白色 Inter 字体，下方灰色副标题显示日期和 GitHub 链接。中央放置一个 80px 的环形评分指示器，根据分数显示绿/黄/红渐变。使用以下 CSS 变量: --color-bg: #0A0A0B, --color-text-primary: #FAFAFA, --color-text-secondary: #A1A1AA。
```

**Prompt 2 — 四维评分卡片**:
```
创建一个 2x2 网格的评分卡片组。每张卡片背景 #111113，边框 1px solid #27272A，圆角 12px。卡片内含维度名称（14px 灰色）、分数（28px 白色粗体）、6px 高的进度条。进度条颜色根据分数: 80+ 使用 linear-gradient(90deg, #22C55E, #4ADE80)，50-79 使用 #F59E0B 系，49 以下使用 #EF4444 系。移动端改为单列堆叠。
```

**Prompt 3 — 难度与推荐指数 Badge**:
```
创建一组水平排列的 Badge。难度 Badge: low=绿色背景(#22C55E20)+绿色文字(#4ADE80)，medium=黄色系，high=红色系。推荐指数使用星级图标 + 蓝色 Badge。所有 Badge 圆角 9999px，内边距 2px 10px，字体 11px/500。使用暗色主题变量。
```

**Prompt 4 — 详细介绍正文区**:
```
创建一个深色主题的 Markdown 渲染区域。容器 max-width 800px，字体 Inter 16px/1.7，文字色 #D4D4D8。H2 标题 22px/600/#FAFAFA。代码块背景 #18181B，边框 #27272A，等宽字体 JetBrains Mono 13px。链接色 #3B82F6，hover 下划线。段落间距 16px。
```

**Prompt 5 — 企微推送卡片预览**:
```
创建一个模拟企微 Markdown 消息的预览卡片。宽度 400px，背景 #18181B，圆角 8px，内边距 16px。包含: 项目名(蓝色链接)、一句话总结(灰色)、评分条(彩色渐变)、难度标签、"查看详情"按钮。字体 14px，行高 1.5。
```

### Iteration Guide

1. **先做移动端** — 报告页 90% 场景是在手机上通过企微打开，桌面端是锦上添花
2. **色彩克制** — 整个页面不超过 5 种颜色：黑白灰 + 蓝色强调 + 评分三色
3. **数据对齐** — 所有分数、维度名左对齐，数值右对齐，形成清晰的扫描线
4. **不要过度设计** — 这是自动化工具不是品牌官网，信息传达效率 > 视觉炫技
5. **自包含优先** — 所有 CSS 内联，无外部字体请求（用系统字体栈回退），无 CDN 依赖
6. **打印友好** — 添加 `@media print` 样式，切换为白底黑字，方便存档
7. **对比度检查** — 所有文字与背景的对比度至少 4.5:1（WCAG AA）
8. **渐进增强** — 评分条动画使用 `prefers-reduced-motion` 媒体查询，尊重用户偏好
9. **错误状态可见** — API 降级时，用橙色虚线边框卡片 + 警告图标标识降级数据
10. **空状态设计** — 无项目时显示一个简约的"今日无新发现"占位图，而非空白页面
