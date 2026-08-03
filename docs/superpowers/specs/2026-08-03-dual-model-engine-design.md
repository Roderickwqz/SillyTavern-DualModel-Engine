# SillyTavern DualModel Engine 设计规格

- 日期：2026-08-03
- 目标平台：SillyTavern 1.18.x 及兼容版本
- 分发方式：独立 GitHub 第三方扩展
- 部署边界：单用户、本机、一对一角色聊天，不使用独立后端
- 交付范围：纯剧情状态、分支回滚、轻量 D20 和自定义规则全部进入首个完整版本
- 状态：设计已确认，可进入实施计划

## 1. 背景与目标

长篇角色扮演会逐渐超出模型上下文窗口。单纯增加总结提示仍会产生事实丢失、关系漂移、数值矛盾和重生成后状态错乱。本项目通过结构化状态和明确的模型职责，把“故事怎么写”“已经发生了什么”“规则结果是什么”分开处理。

项目目标如下：

1. 使用独立状态模型维护可验证、可编辑、可回滚的长期状态。
2. 每轮向剧情模型注入有限且稳定的硬状态，降低关键事实随上下文增长而消失的概率。
3. 支持纯剧情、轻量 D20 和自定义规则预设。
4. 允许按全局、角色和具体聊天配置启用范围。
5. 复用 SillyTavern 的连接、事件、提示注入和聊天存储能力。
6. 保持与现有 Docker `cli-proxy` 部署兼容。

“注意力不衰减”在本设计中定义为：关键状态不依赖模型是否仍能看到原始历史消息，而是由扩展持久化并在每轮重新注入。它不承诺模型拥有无限上下文，也不承诺模型永远百分之百遵守提示。

## 2. 非目标

首个完整版本不包含：

- 完整复刻 D&D 5e 规则。
- SillyTavern 群组聊天；首个完整版本只支持一对一角色聊天。
- 多人实时共享同一战役状态。
- 跨设备状态同步。
- 多浏览器标签页同时修改同一聊天；使用者应在一个活动标签页中运行扩展。
- 服务端数据库和防篡改认证。
- 自建 OpenAI 兼容 FastAPI 中转层。
- 替换 SillyTavern 的聊天界面或主生成管线。
- 让状态模型直接生成最终角色回复。

## 3. 架构方案比较与决策

### 3.1 外部 OpenAI Middleware

中间件伪装 `/v1/chat/completions`，内部调用两个模型。它适合完全独立于 SillyTavern 的客户端，但难以可靠获得角色、聊天、swipe 和删改事件，也需要重新处理流式响应、取消请求和每聊天配置。

结论：不作为首个完整版本方案。

### 3.2 纯 SillyTavern 第三方扩展

扩展直接使用 Connection Profiles、生成事件、Function Tool、`chatMetadata`、角色卡扩展字段和消息 `extra/swipe_info`。它天然知道当前角色、聊天和消息分支，并可以提供配置界面。

结论：首个完整版本采用此方案。

### 3.3 扩展加可选后端

前端扩展负责 UI 和 SillyTavern 集成，后端负责数据库、多人战役和服务端审计。

结论：保留为未来扩展，不进入首个完整版本关键路径。

## 4. 术语与职责

### 4.1 Narrator

剧情模型，负责：

- 角色扮演和对话。
- 场景、动作和事件描写。
- 遵守注入的硬状态。
- 在需要规则裁决时调用 `ResolveD20Check`。
- 根据工具返回值继续生成，而不是自行决定骰点结果。

默认使用 SillyTavern 当前主连接。扩展设置显示该连接，但首个完整版本不在后台静默切换用户的主连接。

### 4.2 Recorder / Director

状态模型，负责在 Narrator 回复完成后：

- 从本轮内容抽取已发生事实。
- 生成 JSON Patch 风格的状态变更。
- 更新关系、任务、物品、伤势、位置和时间。
- 维护未解决剧情线。
- 产生不具强制性的导演建议。

状态模型从 SillyTavern Connection Profiles 中单独选择。API Key、Base URL 和模型凭证由 SillyTavern 管理，扩展不复制保存。

### 4.3 Rule Engine

确定性代码模块，负责：

- 校验规则工具输入。
- 从当前状态读取属性和修正值。
- 生成骰点。
- 计算优势、劣势、总值、DC 和结果等级。
- 限制 HP 等数值边界。
- 生成可审计的判定记录。

## 5. 组件边界

建议组件如下：

```text
src/index.js
  -> extension lifecycle / composition root

st-adapter
  -> isolates SillyTavern events, storage, prompts, profiles and tools

orchestrator
  -> coordinates pre-generation injection, tool calls and post-generation jobs

config-resolver
  -> resolves global, character and chat configuration precedence

model-service
  -> sends Recorder requests through a selected Connection Profile

prompt-injector
  -> renders bounded state and rule instructions for Narrator

state-store
  -> reads/writes chat metadata and message branch snapshots

state-validator
  -> parses and validates state patches and invariants

rollback-manager
  -> restores the correct snapshot after swipe/delete/edit/regenerate

dice-engine
  -> generates unbiased dice values and deterministic D20 calculations

tool-registry
  -> registers ResolveD20Check with SillyTavern Function Calling

preset-manager
  -> imports, validates, binds and exports declarative custom rules

ui/*
  -> settings, current-chat state, audit, diagnostics and editors

rules/*
  -> narrative, d20-lite and custom rule definitions
```

每个模块只暴露稳定接口，不让 UI、存储、模型请求和规则计算互相直接修改内部状态。

### 5.1 宿主适配边界

所有 SillyTavern 私有路径导入和宿主对象访问集中在 `st-adapter`。业务模块不直接导入 `/script.js`、`/scripts/extensions.js`、`/scripts/extensions/shared.js`、`/scripts/events.js` 或 `/scripts/tool-calling.js`。

目标宿主接口如下：

- `ConnectionManagerRequestService.sendRequest`：以指定 Profile 请求 Recorder，不切换 Narrator 当前连接。
- `ToolManager.registerFunctionTool`：注册 `ResolveD20Check`。
- `eventSource` 与 `event_types`：监听生成、消息、swipe、编辑、删除和聊天切换。
- `setExtensionPrompt`：注入有预算的当前状态和规则提示。
- `getContext()`、`chatMetadata`、消息 `extra/swipe_info` 与保存函数：持久化聊天级和分支级数据。

适配器在启动时执行能力探测并返回结构化 capability report。缺少非关键能力时降级，缺少状态持久化或生成事件等关键能力时禁用扩展并显示诊断，不允许业务模块自行猜测替代 API。

### 5.2 构建与运行形式

- 源码使用浏览器 JavaScript ES Modules 和 JSDoc 类型。
- 使用 esbuild 生成 `dist/index.js`，SillyTavern 绝对导入路径保持 external。
- JSON Schema 校验使用随 bundle 打包的 Ajv，不从 CDN 加载运行时代码。
- `manifest.json` 指向已提交的 `dist/index.js` 和 `dist/style.css`，因此用户通过 Git URL 安装后无需运行 npm。
- 开发环境使用 Vitest、jsdom、ESLint 和可注入的宿主/随机源测试替身。

## 6. 配置模型

规则类型与应用范围是两个独立维度。

### 6.1 规则类型

1. `narrative`：通用纯剧情状态。
2. `d20-lite`：纯剧情状态加轻量 D20。
3. `custom`：用户导入或创建的自定义 Schema 和规则预设。

### 6.2 配置作用域

优先级固定为：

```text
当前聊天覆盖 > 角色卡默认 > 扩展全局默认
```

全局扩展设置保存：

- 默认 Recorder Connection Profile。
- 默认规则预设。
- 默认状态更新策略。
- 默认错误策略和显示选项。

角色卡扩展字段保存：

- 新聊天是否默认启用。
- 默认规则预设 ID。
- 可选的角色初始状态模板。

当前聊天元数据保存：

- 当前聊天是否启用。
- 对全局和角色默认值的覆盖。
- 当前状态版本和最新状态。
- 后台状态任务信息。

消息分支数据保存：

- 生成前状态版本。
- 本轮规则记录。
- 状态 Patch。
- 生成后快照。
- 当前 swipe 对应的审计信息。

## 7. 状态数据模型

逻辑状态分为三层。

### 7.1 Canonical State

已确定且会强制注入 Narrator 的事实：

```json
{
  "version": 18,
  "scene": {
    "location": "废弃教堂地下室",
    "time": "午夜"
  },
  "characters": {
    "艾琳": {
      "attitude": "警惕",
      "trust": 42,
      "injuries": ["左臂轻伤"]
    }
  },
  "inventory": ["生锈钥匙"],
  "quests": [],
  "world_facts": [],
  "promises": [],
  "secrets": []
}
```

### 7.2 Open Threads

已建立但尚未解决的剧情线。它们会以较低优先级注入，并带有来源消息标识。

### 7.3 Director Hints

状态模型提出的下一步事件候选。它们是软建议，不属于硬事实，Narrator 可以忽略。

### 7.4 Patch 格式

Recorder 不返回完整替换状态，而返回带版本和原因的操作：

```json
{
  "base_version": 17,
  "operations": [
    {
      "op": "replace",
      "path": "/characters/艾琳/trust",
      "value": 45,
      "reason": "玩家冒险救下艾琳"
    },
    {
      "op": "add",
      "path": "/inventory/-",
      "value": "生锈钥匙",
      "reason": "从守卫尸体上取得"
    }
  ]
}
```

扩展必须在提交前验证 JSON、路径白名单、字段类型、数值范围、锁定字段和 `base_version`。

### 7.5 持久化信封

聊天级数据统一放在插件命名空间下：

```json
{
  "dualModelEngine": {
    "schemaVersion": 1,
    "stateVersion": 18,
    "activeSnapshot": {},
    "configOverrides": {},
    "taskStatus": {
      "state": "idle",
      "requestId": null
    },
    "lastCommittedRequestId": "req-0182"
  }
}
```

每个 AI 消息的每个 swipe 在 `swipe_info[swipeId].extra.dualModelEngine` 保存：

```json
{
  "branchId": "branch-0182-0",
  "baseStateVersion": 17,
  "baseSnapshot": {},
  "segments": [
    {
      "requestId": "req-0182",
      "userMessageId": "msg-user-0181",
      "assistantTextHash": "sha256:...",
      "checks": [],
      "patch": {},
      "postSnapshot": {}
    }
  ],
  "status": "committed"
}
```

扩展为相关消息分配自己的 UUID 并保存在消息 `extra` 中。数组下标只用于访问当前 SillyTavern 内存对象，不作为持久身份，因为删除历史消息后下标会变化。文本哈希用于发现消息内容被宿主或其他扩展改写，不用于身份认证。

`segments` 的第一个元素代表初次生成，后续元素代表同一 swipe 上的 `continue`。每个 segment 只处理新增文本和新增工具记录，最终 `postSnapshot` 是该分支当前有效状态。

### 7.6 数据版本与迁移

- `schemaVersion` 表示扩展持久化格式版本，`stateVersion` 表示剧情状态提交版本，两者不得混用。
- 每次加载聊天先运行纯函数迁移；迁移前深拷贝原数据，失败时保留原始数据并进入只读诊断模式。
- 导入的规则预设带独立 `presetVersion`，不允许通过修改规则预设静默重解释既有快照。
- 规则预设变更只影响下一次提交；需要重解释历史时必须由用户显式触发“从快照重算”。

## 8. 每轮执行流程

### 8.1 通用路径

1. 玩家消息进入当前聊天。
2. Orchestrator 读取生效配置和当前有效状态快照。
3. 如果上一轮 Recorder 仍在运行，本轮生成等待它提交或明确失败。
4. Orchestrator 为本次生成捕获 chat、message、swipe、base state version 和 request identity。
5. Prompt Injector 将压缩后的 Canonical State、Open Threads、规则说明和必要审计摘要注入 Narrator。
6. Narrator 生成回复并按需调用规则工具。
7. 回复完成后立即向用户显示。
8. Recorder 后台读取旧状态、本轮玩家消息、工具结果和最终回复；`continue` 只传新增文本。
9. Validator 校验 Recorder Patch。
10. State Store 通过 compare-and-swap 提交新版本，并附着到当前消息、swipe 和 segment。
11. 下一轮使用新版本。

### 8.2 状态注入预算

Canonical State 必须有固定预算，避免状态本身无限增长。注入顺序为：

1. 当前场景和角色即时状态。
2. 当前任务、承诺和强约束。
3. 与最近消息直接相关的开放剧情线。
4. 软性导演建议。

历史事件保存在审计记录中，但不全部重复注入。状态面板允许用户查看完整历史。

默认注入预算为 1200 tokens，可在全局设置中调整。Prompt Injector 必须通过宿主 tokenizer 估算预算；若 tokenizer 不可用，则按字符数保守裁剪。裁剪只降低 Open Threads 和 Director Hints，不能静默丢弃当前地点、当前角色数值、锁定事实和未完成正式检定结果。

## 9. D20 流程

### 9.1 主路径：Function Tool

当 `cli-proxy` 和 Narrator 支持 OpenAI 风格工具调用时：

```text
Narrator 请求 ResolveD20Check
  -> 扩展校验请求
  -> Rule Engine 读取角色数据并掷骰
  -> 返回结构化结果
  -> Narrator 根据结果继续生成
```

工具输入示例：

```json
{
  "actor": "玩家",
  "action": "撬开箱锁",
  "ability": "dexterity",
  "skill": "sleight_of_hand",
  "dc": 15,
  "advantage": "normal",
  "reason": "箱锁已经生锈，但结构复杂"
}
```

工具输出示例：

```json
{
  "check_id": "check-0182",
  "rolls": [14],
  "selected_roll": 14,
  "ability_modifier": 2,
  "proficiency_bonus": 2,
  "total": 18,
  "dc": 15,
  "outcome": "success"
}
```

正式骰点使用浏览器 Web Crypto 的 `crypto.getRandomValues`，并通过拒绝采样消除取模偏差。测试可以注入可预测随机源，但生产路径不得回退到由模型提供骰点。

### 9.2 兼容路径：生成前裁决

如果 `cli-proxy` 不支持工具调用，扩展在 Narrator 正式生成前调用 Recorder/Adjudicator，生成结构化检定请求；代码完成裁决后将结果作为硬状态注入 Narrator。该路径多一次模型请求，但不依赖生成中的工具循环。

### 9.3 判定策略

- `automatic-tool`：Narrator 可以按规则直接请求检定，延迟最低，但依赖模型主动调用工具。
- `enforced-preflight`：Adjudicator 在 Narrator 生成前检查玩家动作；一旦认定需要检定，Narrator 必须接收已经完成的裁决结果。
- `confirm`：使用生成前检查，并在掷骰前显示检定、DC 和理由供用户确认。
- `manual`：只有用户主动触发时才执行正式检定。

默认策略为 `automatic-tool`。重视规则一致性的战役使用 `enforced-preflight`，重视人工裁决的战役使用 `confirm`。当工具调用不可用时，`automatic-tool` 自动降级为 `enforced-preflight`，并在状态面板显示降级原因。

### 9.4 轻量 D20 范围

首个完整版本支持：

- 基础属性修正。
- 技能熟练。
- 优势和劣势。
- DC。
- 自然 1 和自然 20 策略。
- HP、临时 HP。
- 伤害骰。
- 基础状态效果。

不包含完整 D&D 5e 职业、法术、专长、抗性和行动经济。

### 9.5 判定不变量

- 工具参数中的属性值、技能加值、熟练加值和 HP 不能作为权威输入；Rule Engine 必须从当前状态读取。
- Narrator 可以提出 action、ability、skill、DC 和理由，Validator 根据规则预设限制字段和值域。
- 骰点使用 Web Crypto 拒绝采样；生产环境没有 Web Crypto 时禁用正式判定，不回退到 `Math.random`。
- 同一分支的正式检定记录一经提交不可原地修改；重新判定创建新记录并引用被替代的 `checkId`。
- 自然 1、自然 20、优势/劣势抵消、临时 HP、伤害下限和最大 HP 均由规则预设与代码共同约束。
- 工具返回值同时写入 Narrator 工具结果、分支审计和 Recorder 输入；三处使用同一不可变记录。

## 10. 自定义规则预设

自定义规则是声明式 JSON 数据，不是插件脚本。预设包含：

- `id`、`name`、`presetVersion` 和兼容的持久化版本范围。
- Canonical State JSON Schema 和初始状态模板。
- Recorder 可修改路径、锁定路径、数值范围和集合大小限制。
- 状态注入分区、优先级和显示标签。
- 状态面板卡片和字段编辑器配置。
- 可选 D20 能力值、技能到能力映射、熟练路径、HP 路径和自然骰策略。

首个完整版本不提供任意公式语言。自定义预设可以重新命名和映射内置轻量 D20 原语，但不能导入 JavaScript、正则替换代码或动态网络地址。这样既能覆盖不同剧本的状态结构，又不会把规则预设变成代码执行入口。

导入流程固定为：解析 JSON、校验预设 Schema、编译状态 Schema、验证初始状态、检查路径交叉冲突、显示变更摘要、用户确认后保存。导出时只包含声明式预设和可选初始状态，不导出 API 凭证、聊天历史或审计记录。

预设保存于扩展全局设置，角色卡只保存默认预设 ID 和初始状态模板，聊天保存绑定时的预设 ID 与版本。删除仍被角色或聊天引用的预设时必须阻止删除，或要求用户先选择替代预设。

## 11. 与官方 D&D Dice 的关系

官方 D&D Dice 是可选辅助扩展，不是依赖项。

- 官方菜单和 `/roll` 继续用于手动娱乐掷骰。
- 本扩展的 `ResolveD20Check` 是正式剧情判定的唯一权威来源。
- 两者共存时，建议关闭官方 D&D Dice 的 Function Tool，避免 Narrator 选择只返回骰点、但不绑定状态的通用工具。
- 本扩展不能依赖官方扩展未公开的内部函数。

## 12. Swipe、重生成与回滚

每个 AI 回复 swipe 必须保存自己的：

- 稳定 branch identity。
- 生成前状态版本。
- 检定记录。
- 初次生成和各次 `continue` 的 segment。
- 每个 segment 的 Recorder Patch 与生成后状态快照。

行为规则：

- 切换到已有 swipe：恢复该 swipe 对应状态。
- 创建新 swipe：默认沿用原正式骰点，防止刷回复重掷。
- 显式“重新判定”：创建新骰点和新审计记录。
- `continue`：在当前 swipe 追加 segment，只把新增文本交给 Recorder，不重复执行已经完成的判定。
- 删除消息：恢复最后一个仍存在的有效快照。
- 编辑历史消息：使该消息之后的快照失效，并从最后有效快照按当前可见分支顺序重新计算；重算前显示影响范围并要求用户确认。
- 生成期间切换聊天：属于旧聊天的异步结果不得提交。
- 删除非当前 swipe：删除其分支记录，不改变当前状态；删除当前 swipe 后恢复宿主选中的替代 swipe。
- 其他扩展改写消息文本且哈希不匹配：把后续记录标记为 stale，不自动猜测差异。

## 13. 并发与幂等

每个聊天维护单独的异步任务队列。Recorder 任务至少携带：

- chat identity。
- source message identity。
- source swipe identity。
- base state version。
- request identity。

同一个 request identity 只能提交一次。提交时如果聊天、swipe 或状态版本已经变化，结果直接丢弃，不覆盖新状态。

首个完整版本虽然只支持一个当前浏览器标签页和一对一聊天，仍必须把聊天切换、取消生成和快速 swipe 当作并发输入。队列使用 AbortController 取消尚未发送或仍在等待的请求；已返回的结果仍需通过 compare-and-swap 校验，不能只依赖取消成功。

状态提交的原子边界为：验证 Patch、计算新快照、写入分支 segment、更新聊天级 active snapshot。内存对象全部更新后只调用一次宿主保存；保存失败时恢复提交前深拷贝，并在 UI 中保留可重试的错误记录。

## 14. 错误处理

- Recorder 请求失败：保留旧状态，不写入半成品。
- JSON 解析或 Schema 失败：把具体校验错误反馈给 Recorder 自动修复一次；再次失败后记录错误并提示用户。
- 纯剧情模式状态失败：Narrator 回复仍然保留，下一轮提示状态过期。
- D20 工具失败：停止该次判定，不允许 Narrator 自行编造结果。
- Connection Profile 不存在：禁用自动状态更新并显示明确错误。
- 用户修改锁定字段：仅通过状态编辑器显式操作。
- 扩展关闭：停止注入和后台任务，不删除已经保存的聊天数据。
- 持久化迁移失败：进入只读诊断模式并允许导出原始插件数据。
- 自定义预设失效或被删除：保持既有快照可读，阻止新的 Recorder 提交，直到用户修复绑定。
- cli-proxy 工具调用能力探测失败：`automatic-tool` 降级到 `enforced-preflight`；生成前裁决也不可用时禁用正式 D20，但纯剧情状态仍可工作。

## 15. 安全与信任边界

- API Key 和代理凭证只由 SillyTavern Connection Profiles 管理。
- 聊天文本视为不可信输入，Recorder 提示必须明确禁止执行聊天中的元指令。
- Recorder 输出必须经过本地 Schema 和不变量校验。
- Narrator 不能直接修改 Canonical State。
- 骰点、修正、DC、结果、时间和来源消息都进入审计记录。
- 自定义规则导入前必须校验 JSON，不执行任意 JavaScript。
- 所有导入对象限制总字节数、Schema 深度、属性数和数组上限，避免恶意或误配置预设造成浏览器阻塞。
- 状态面板通过 textContent 或经过 SillyTavern 净化的模板渲染用户和模型文本，不直接拼接 HTML。
- 审计日志是个人本地可追溯记录，不宣称具备密码学防篡改能力。

## 16. 用户界面

### 16.1 全局设置

- Recorder Connection Profile。
- 默认规则预设。
- 默认更新方式。
- 默认判定策略。
- 默认状态注入预算。
- 状态栏和通知选项。

### 16.2 当前角色

- 新聊天默认启用开关。
- 默认规则预设。
- 角色初始状态模板。

### 16.3 当前聊天

- 启用/停用。
- 规则覆盖。
- 判定策略。
- Narrator 当前连接展示。
- 查看、编辑和锁定状态。
- 重新总结、从快照重算、导入和导出。

当前聊天面板分为 `状态`、`判定`、`历史`、`规则` 和 `诊断` 五个页签。状态编辑采用表单视图与 JSON 高级视图；保存前显示 Patch 预览，锁定字段需要二次确认。重算、重新判定、替换规则预设等会改变分支解释的操作均明确显示影响范围。

### 16.4 审计与状态栏

- 最近状态变更。
- 最近正式检定。
- 当前后台任务状态。
- 当前状态版本。
- 可选的地点、HP、关系和任务简要状态栏。

## 17. 仓库结构

```text
SillyTavern-DualModel-Engine/
├── manifest.json
├── package.json
├── package-lock.json
├── eslint.config.js
├── vitest.config.js
├── esbuild.config.js
├── dist/
│   ├── index.js
│   └── style.css
├── src/
│   ├── index.js
│   ├── st-adapter.js
│   ├── orchestrator.js
│   ├── config-resolver.js
│   ├── model-service.js
│   ├── prompt-injector.js
│   ├── state-store.js
│   ├── state-validator.js
│   ├── rollback-manager.js
│   ├── task-queue.js
│   ├── dice-engine.js
│   ├── rule-engine.js
│   ├── tool-registry.js
│   ├── preset-manager.js
│   ├── migrations.js
│   ├── ui/
│   │   ├── controller.js
│   │   ├── settings.html
│   │   └── style.css
│   └── rules/
│       ├── narrative.js
│       ├── d20-lite.js
│       └── custom.js
├── schemas/
│   ├── state.schema.json
│   ├── patch.schema.json
│   ├── d20.schema.json
│   └── preset.schema.json
├── tests/
│   ├── unit/
│   ├── integration/
│   ├── e2e/
│   └── fixtures/
└── docs/
```

该结构表达模块边界，不要求第一批提交一次创建所有空文件。

## 18. 测试策略

### 18.1 单元测试

- 配置继承优先级。
- Patch Schema 和路径白名单。
- 状态版本冲突。
- 持久化格式迁移与迁移失败回退。
- 数值不变量。
- D20 普通、优势、劣势和边界结果。
- Web Crypto 拒绝采样和随机源故障。
- 幂等提交。
- swipe、continue segment、删除和编辑后的快照选择与回滚。
- 自定义规则导入限制、路径冲突和版本绑定。
- 注入预算裁剪不删除硬约束。

### 18.2 集成测试

- 使用模拟 Connection Profiles 返回合法和非法结构化输出。
- Narrator 工具调用到规则结果的完整循环。
- Recorder 失败和重试。
- 切换聊天时丢弃旧任务。
- `cli-proxy` 工具调用和流式响应能力探测。
- 宿主保存失败时的内存回滚。
- 从失效点顺序重算并在中途失败时保持最后有效快照。

### 18.3 端到端测试

- 安装扩展并按聊天启用。
- 生成剧情并在下一轮看到新状态注入。
- 切换 swipe 后恢复对应状态。
- 删除、编辑和继续生成不污染状态。
- D20 结果在剧情、状态栏和审计记录中保持一致。
- 导入自定义规则、绑定角色、覆盖聊天并重新打开聊天后仍保持一致。
- 在目标 SillyTavern release 分支完成手工安装冒烟测试。

## 19. 开发阶段

### 阶段 0：能力探测

验证 `cli-proxy` 的 `tools`、`tool_calls`、流式工具调用和两个 Connection Profile。探测结果决定使用主路径还是兼容路径，但不改变其他设计。

### 阶段 1：纯剧情状态 MVP

完成扩展骨架、配置作用域、Recorder 选择、生成后 Patch、状态注入、查看编辑和基本错误处理。

### 阶段 2：分支一致性

完成 swipe、重生成、继续生成、删改消息、快照、回滚、幂等和任务队列。

### 阶段 3：轻量 D20

完成 `ResolveD20Check`、规则计算、判定策略、审计记录和官方 D&D Dice 共存策略。

### 阶段 4：自定义规则

完成规则预设导入导出、角色绑定、聊天覆盖和状态栏配置。

阶段 1 的“纯剧情状态 MVP”只是内部可运行里程碑，不是对外宣称的首个完整版本。首个完整版本必须完成阶段 0 至阶段 4，并通过全部验收标准；各阶段保持可测试、可回退，避免一次性集成全部风险。

## 20. 验收标准

1. 扩展可通过 GitHub URL 在未修改的 SillyTavern 中安装。
2. 用户可以为当前聊天启停扩展并选择独立 Recorder Connection Profile。
3. Recorder 失败或输出非法 JSON 时，既有状态不被破坏。
4. 保存并重新打开聊天后，状态与版本仍然存在。
5. Narrator 每轮都能收到当前有效 Canonical State。
6. D20 骰点和最终计算由代码产生，Narrator 不能修改审计结果。
7. 切换已有 swipe 会恢复该分支状态；默认重生成不会偷偷重掷。
8. 删除或编辑历史消息不会让失效状态继续成为当前状态。
9. 自定义规则可以绑定到角色，并在具体聊天中覆盖。
10. 在长对话中，即使原始事实离开模型上下文窗口，仍能从持久化状态重新注入关键事实。
11. `continue` 只处理新增文本，重复检定和重复 Patch 不会发生。
12. 扩展检测到群组聊天时保持禁用并清楚说明当前版本不支持，而不是写入错误格式的数据。
13. 预设、聊天数据和分支记录经过版本迁移后仍可读取；迁移失败时原数据可导出。
14. Git URL 安装使用仓库内已构建的 `dist`，用户不需要 npm 或额外服务。

## 21. 后续开发入口

下一步生成逐文件实施计划。实施计划从扩展骨架和宿主能力适配开始，将 `cli-proxy` 工具调用探测作为阶段 0 的可执行诊断，再进入纯剧情状态、分支一致性、D20 和自定义规则。不得先实现 D20 而跳过状态版本、分支和回滚基础。
