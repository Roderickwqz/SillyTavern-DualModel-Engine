# LangGraph 长期 RPG 引擎与 RPG Companion 兼容版设计规格

- 日期：2026-08-10
- 状态：设计已确认，等待书面规格复核
- 使用场景：单用户、本机、长期一对一文本角色扮演
- 前端：SillyTavern + RPG Companion 兼容版
- 后端：FastAPI + LangGraph
- 权威存储：SQLite；JSONL 审计；YAML 规则与 Preset
- 默认规则模式：纯剧情 `narrative`
- 首个可选规则集：D&D 2024 修订版第五版（5.5e）

## 1. 背景与目标

现有 DualModel Engine 以 SillyTavern 扩展形式在主回复结束后调用 Recorder，再把状态保存在聊天元数据中。该方案能处理基础状态、分支和轻量 D20，但用户难以直接观察后台任务，也不适合承载可独立检查、可扩展、超长运行的本地游戏世界。

新方向保留 SillyTavern 的角色卡、聊天体验和 RPG Companion 的 Tracker UI，把状态、规则、长期记忆和一致性控制迁入可独立运行的 LangGraph 后端。目标如下：

1. 单次战役可运行数千至数万回合，关键事实不依赖原始聊天仍在模型上下文中。
2. 人物、地点、组织、技能、关系和任意自定义属性可通过会话新增或修改。
3. 人物性格与关系连续发展，并能解释从前状态、当前状态和变化原因。
4. D&D 判定、骰点、战斗和资源变化由确定性代码执行并审计。
5. RPG Companion 保留现有 Tracker、主题、人物面板、背包、任务和 per-swipe 显示体验。
6. 所有重要数据在本地持久化、可导出、可恢复、可审计。

本设计不承诺模型拥有无限上下文。它通过结构化状态、事件历史、阶段摘要和按需检索，让重要信息不依赖无限 Prompt。

## 2. 核心决策

### 2.1 唯一状态权威

LangGraph 后端及其 SQLite 数据库是唯一权威状态。RPG Companion 只显示后端提交后的可见投影；SillyTavern 聊天记录和 RPG Companion 的 per-swipe 数据是缓存与用户界面，不参与规则结算。

### 2.2 不同时运行 DualModel Engine

新架构不运行当前 DualModel Engine。原因是现有 DME 在主回复结束后异步请求 Recorder 并提交状态，而 RPG Companion Together 模式会在收到回复时立即解析 Tracker；二者并行会产生时序竞争和双写。

现有项目中以下设计与行为测试可作为迁移参考：

- JSON Schema、Patch 校验和状态不变量。
- 骰子随机源、D20 规则和不可变判定记录。
- compare-and-swap、事务回滚和陈旧任务保护。
- swipe、重生成、删除、编辑和分支恢复测试。

浏览器宿主适配、Connection Profile Recorder 调用和 DME 自有 UI 不进入新运行时。

### 2.3 RPG Companion 兼容版

采用 RPG Companion 的 AGPL-3.0 代码制作兼容版，不额外安装第二个桥接插件。兼容版保留原界面和数据格式，增加动态属性渲染、只读权威模式和诊断信息。分发时保留许可证、版权说明并公开对应修改源码。

## 3. 产品边界

首个完整版本包含：

- OpenAI Chat Completions 兼容入口。
- 单战役、多人物、地点、组织和任务。
- 动态属性注册表和会话驱动修改。
- 人格、关系和发展时间线。
- 四级 Audience 信息隔离。
- SQLite 持久化、JSONL 审计、快照、备份和导出。
- swipe 分支识别与恢复。
- 每个 Campaign 独立启停的 D&D 2024 / 5.5e 骰子与完整核心战斗规则。
- RPG Companion Together 模式输出和动态 Tracker。

首版不包含：

- 多用户实时共同编辑。
- 云端账户、跨设备同步和托管数据库。
- RPG Companion 面板内容直接回写后端；权威修改通过会话命令完成。
- 自动合并互相矛盾的 swipe 分支。
- 让 LLM 直接执行 SQL、修改 SQLite 或自行决定权威骰点。

## 4. 总体架构

```text
SillyTavern
└── RPG Companion 兼容版
    ├── 原有 User Stats / Info Box / Characters
    ├── Inventory / Quests / Themes
    ├── 动态 attributes 渲染器
    ├── per-swipe 显示缓存
    └── 只读权威模式

FastAPI / OpenAI-compatible API
└── LangGraph
    ├── Request Normalizer
    ├── Intent Router
    ├── Entity Resolver
    ├── Memory Retriever
    ├── Rule / Dice Engine
    ├── Change Proposal Builder
    ├── Authorization Gate
    ├── State Validator
    ├── Narrative Generator
    ├── Consistency Critic
    ├── Transaction Committer
    └── RPG Companion Presenter

Local persistence
├── SQLite：权威状态、索引和快照
├── JSONL：追加式事件和审计
├── YAML：规则、D&D 配置和 Tracker Preset
├── JSON：角色卡与 Campaign 导入导出
└── CSV：统计分析导出
```

每个组件只通过结构化输入输出交互。LLM 组件可以生成候选内容，但只有确定性 Python 组件能验证和提交状态。

## 5. 单回合数据流

1. SillyTavern 向本地 `/v1/chat/completions` 发送当前选中分支的消息和固定 `campaign_id`。
2. Request Normalizer 识别并移除 RPG Companion 在 Together 模式注入的合成 Tracker 指令，保留真实玩家动作和角色卡上下文。
3. Intent Router 区分普通行动、状态查询、显式状态修改、剧情推断和规则判定。
4. Entity Resolver 将名字、别名和代词解析为稳定实体 ID；歧义时不猜测。
5. Memory Retriever 按实体、地点、任务、关系、Audience 和当前分支取得有限上下文。
6. Rule Engine 在需要时执行权威骰点、战斗与资源结算。
7. Narrative Generator 只生成剧情草稿；它不能提交状态。
8. Change Proposal Builder 从玩家显式意图和剧情结果产生类型化变更候选。
9. Authorization Gate 根据来源决定立即应用或持久化为待确认提案。
10. State Validator 校验属性类型、数值范围、Audience、规则不变量、分支和基础版本。
11. Consistency Critic 检查剧情是否与已计算的规则结果和候选状态一致；必要时只重写剧情，不改变规则结果。
12. Transaction Committer 以单个 SQLite 事务提交状态、事件、审计、回合和快照版本。
13. RPG Companion Presenter 从提交后的权威状态生成 Tracker JSON。
14. API 返回一个统一 Tracker JSON code block 加正常剧情；提交失败不返回伪成功 Tracker。

首版关闭流式输出。后端完成校验和提交后再返回完整回复，避免用户看到尚未成为权威状态的半段剧情。流式输出只能在以后引入可撤销协议后启用。

## 6. 动态属性注册表

### 6.1 固定核心与动态扩展

固定核心只保存系统稳定标识和事务所需字段：

- Campaign、branch、turn、entity 和版本 ID。
- 人物名称、别名、实体类型和年龄状态。
- 当前时间、地点和活动场景。
- 战斗参与者、行动顺序和规则版本。
- 属性定义、属性值和 Audience。

技能、性格、资源、关系维度、声望、语言、专长和自定义 Tracker 使用类型化动态属性注册表，不通过每次修改完整 JSON Schema 实现。

### 6.2 属性定义

```yaml
key: alchemy
label: 炼金术
category: skill
type: number
min: 0
max: 100
unit: null
display: bar
audiences:
  - engine
  - npc_agent
  - narrator
  - player_ui
aliases:
  - 炼金
  - Alchemy
```

支持 `number`、`integer`、`boolean`、`text`、`enum` 和 `list`。显示提示支持 `bar`、`number`、`badge`、`text`、`list` 和 `progress`。显示提示不改变后端类型和规则语义。

### 6.3 属性值

```yaml
entity_id: erin
attribute_key: alchemy
value: 35
state_version: 28
updated_turn_id: turn-102
```

定义和值分开保存。同一 Campaign 中语义和类型一致的属性复用定义；名字相近但类型或语义不一致时产生待确认冲突，不自动合并。

### 6.4 新人物

会话可以创建此前不存在的人物：

```text
新增人物艾琳：半精灵炼金术师，性格谨慎，目前在银月城。
```

系统创建稳定实体 ID、名称、别名、基础事实和相应属性。仅提及陌生名字不自动建立完整人物；系统可以创建低置信度实体候选，并在人物首次成为剧情参与者时确认或补全。

## 7. 会话驱动修改与授权

### 7.1 显式修改

明确的新增、设置和增减指令可以直接提交：

```text
给艾琳新增技能“炼金术”，当前 35，最高 100。
把艾琳的信任调整为 60。
给玩家增加两瓶治疗药水。
```

普通技能、公开标签、备注和非破坏性属性可直接执行。删除人物、重置档案、修改核心身份、大范围覆盖和跨分支操作必须二次确认。

### 7.2 剧情推断

剧情表现只能产生待确认提案：

```yaml
proposal_id: p-102
base_state_version: 28
branch_id: main
entity_id: erin
operation: add_attribute
attribute_key: alchemy
suggested_value: 40
reason: 艾琳表现出专业药剂分析能力
status: pending
```

玩家确认时重新检查 Campaign、分支和基础版本。上下文已经变化的旧提案不能盲目应用。

### 7.3 实体歧义

Entity Resolver 使用稳定 ID、别名、当前场景和最近指代消解实体。存在两个合理候选时返回简短询问，不以概率最高者直接写入。

## 8. Audience 信息隔离

每个事实和动态属性使用 Audience 集合：

| Audience | 可见范围 |
|---|---|
| `engine` | 规则、状态机、数据库和审计 |
| `npc_agent` | 对应 NPC 的决策与心理模型 |
| `narrator` | 允许用于当前剧情表达的信息 |
| `player_ui` | 允许发送到 RPG Companion 和玩家查询的信息 |

默认规则：

- 普通技能、装备和公开身份：四方可见。
- 思想、秘密和真实动机：默认仅 `engine + npc_agent`。
- 精确关系数值：默认隐藏；可观察态度另存为可见属性。
- 玩家可通过显式命令修改 Audience。
- Narrative Generator、NPC Agent 和 Presenter 分别读取过滤后的投影，不接收完整数据库对象。

秘密被剧情合法揭露时，提交一条 Audience 变化事件。历史仍记录揭露前不可见、揭露后可见的时间范围。

## 9. 人格、关系与人物发展

### 9.1 多维连续人格

人物发展不通过覆盖单个形容词实现。每个维度保存 baseline、current、变化速度和最近证据，例如开放度、好奇心、冲动、承诺偏好、公开拘谨和风险容忍度。用户可以配置显示标签，但规则依据始终是类型化维度与上下文组合。

### 9.2 变化事件

```yaml
character_id: erin
trait: openness
before: 35
delta: 8
after: 43
cause: 连续多次主动探索新的关系观念
turn_id: turn-182
source: narrative_development
```

普通、重要和重大事件使用不同变化上限。LLM 提出 delta 和证据，Python 检查重复事件、范围、变化速度和发展惯性。单句剧情不能造成没有依据的极端人格翻转。

### 9.3 发展阶段

系统根据多维状态和关键事件形成带起止回合的阶段，例如“保守拘谨”“好奇与矛盾”“主动探索”“开放关系观”。阶段是可重建摘要，不替代原始数值和变化事件。发展允许反向和分化，例如公开态度与私人偏好不同。

### 9.4 成人亲密属性

成人亲密相关属性仅对 `age_status: adult` 的明确成年人物启用。年龄未知或未成年实体不能创建或计算该类属性。开放度、吸引、好感、过去行为和关系身份不能替代具体场景中的当次意愿；行为判断必须独立考虑对象、场景、边界、风险和当次决定。

## 10. 超长记忆

### 10.1 原始事件层

所有已提交的重要事实和事件永久保存在 SQLite，并追加到 JSONL 审计。事件包含 Campaign、branch、turn、参与者、地点、类型、内容、重要度、Audience 和来源。

### 10.2 当前状态层

为规则和生成快速读取实体当前有效事实、属性、关系、物品、任务、地点和战斗状态。事实变化使用 `valid_from`、`valid_until` 和 `superseded_by`，不删除旧值。

### 10.3 发展摘要层

按人物、关系、任务和剧情线生成阶段摘要。每个摘要引用原始事件 ID，因此摘要可以重新生成和纠错，不会成为唯一事实来源。

### 10.4 检索索引层

首版使用 SQLite FTS5、实体关系和结构化过滤。向量索引为可选增强，不是正确性依赖。检索按当前分支和 Audience 过滤，并组合：

- 当前权威状态。
- 当前场景人物和地点。
- 最近高重要度事件。
- 与本次行动有关的长期事件。
- 未完成承诺、任务和冲突。
- 人格、关系和发展阶段摘要。

“遗忘”只降低检索优先级，不删除原始事件。关键承诺、身份变化、人格转折和规则后果保持长期高优先级。

## 11. D&D 2024 / 5.5e、骰子与战斗

### 11.1 Campaign 规则配置

API 服务不全局绑定 D&D。每个 Campaign 独立持久化规则配置，默认纯剧情且不启用 D&D：

```yaml
rules:
  mode: narrative
  enabled: false
  version: null
```

支持三种模式：

| Mode | 行为 |
|---|---|
| `narrative` | 不执行 D&D 判定，只运行人物、关系、记忆和动态属性 |
| `dnd-2024` | 启用 D&D 2024 / 5.5e 骰子、战斗和资源规则 |
| `custom:<preset_id>` | 使用 Campaign 绑定的声明式自定义规则 |

首个内置 D&D 规则集 ID 为 `dnd-2024`。Campaign 启用它时保存规则集 ID、数据版本和勘误版本；后续代码或规则数据升级不得静默重新解释旧回合。切换规则版本必须经过显式迁移、差异预览和确认。

规则启停只能来自用户显式命令或本地 Campaign 管理接口，不能由 Narrator、剧情推断或普通 API 请求参数自动改变：

```text
/rules status
/rules enable dnd-2024
/rules disable
/rules set narrative
```

启用 D&D 时先创建状态快照，检查参与人物是否具备所需角色数据，并要求补全或导入缺失字段；成功后才把 `mode` 和 `enabled` 原子提交为 `dnd-2024` 与 `true`。关闭时停止自动判定、资源消耗和 D&D Tracker 投影，但保留角色卡、战斗记录和规则审计，之后可以恢复。活动战斗中关闭规则需要确认，并保存战斗暂停快照。

所有模式使用相同 OpenAI 兼容 API 和 `campaign_id`。不得通过每回合 Custom Body 的 `dnd_enabled` 一类字段控制规则，避免单次请求配置错误改变战役语义。

规则实现以公开的 2024 Free Rules / SRD 5.2.1 核心机制为边界。项目不复制或捆绑未获授权的专有职业、子职、法术、怪物和冒险文本；额外内容通过用户提供或合法授权的数据包扩展。这里的“完整战斗”指核心战斗过程和规则组合完整，不表示首版内置所有已出版角色选项与怪物资料。

官方参考：

- https://www.dndbeyond.com/sources/dnd/br-2024
- https://media.dndbeyond.com/compendium-images/srd/5.2/SRD_CC_v5.2.1.pdf

### 11.2 完整核心战斗范围

当 `rules.mode` 为 `dnd-2024` 且 `rules.enabled` 为 `true` 时，首个完整规则集必须覆盖：

- 战斗开始、突袭、先攻、同先攻处理、轮次和回合生命周期。
- 速度、分段移动、困难地形、起身、爬行、跳跃、攀爬、游泳和强制移动。
- Action、Bonus Action、Reaction、Movement 和免费物体交互的使用与重置。
- Attack、Dash、Disengage、Dodge、Help、Hide、Influence、Magic、Ready、Search、Study 和 Utilize。
- 近战、远程、法术攻击、攻击距离、长距离、遮蔽、隐蔽、优势、劣势和重击。
- 徒手攻击、擒抱、推撞、脱离擒抱和尺寸限制。
- 轻型武器、双持、装填、投掷、触及和机会攻击。
- 2024 Weapon Mastery 的 Cleave、Graze、Nick、Push、Sap、Slow、Topple 和 Vex，以及每回合触发限制。
- 伤害骰、伤害类型、抗性、易伤、免疫、伤害减免和多来源结算顺序。
- 当前 HP、最大 HP、临时 HP、治疗、0 HP、昏迷、死亡豁免、稳定和瞬间死亡。
- 2024 核心 Conditions、Exhaustion 等级及其对 d20 检定和速度的影响。
- 法术位、Magic Action、每回合消耗一个法术位的限制、豁免、范围、区域、持续时间和 Concentration。
- Short Rest、Long Rest、Hit Point Dice、资源恢复和休息中断。
- 坐骑、水下、坠落、环境伤害和临时危险的规则接口。
- 怪物多重攻击、Recharge、回合开始/结束触发、Reaction 和可声明的特殊动作。
- 玩家确认策略、反应窗口、资源消耗、回合日志和可重放审计。

职业能力、专长、法术、怪物和物品以数据驱动的触发器与效果定义接入同一引擎，不能在 Narrator Prompt 中以自然语言替代权威结算。

### 11.3 权威随机和授权

LLM 或 RPG Companion 自带骰子均不是权威随机源。Python Rule Engine 负责：

- 解析经过授权的检查和攻击。
- 从状态读取属性、熟练、优势、劣势、状态和资源。
- 使用可注入的加密安全随机源生成骰点。
- 结算命中、伤害、豁免、临时生命、专注、行动资源和状态。
- 在同一事务中提交游戏状态和不可变判定记录。

普通玩家行动中，明确尝试需要判定的动作视为同意执行普通骰点。消耗稀缺资源、使用反应、不可逆选择或动作含义不明确时先询问。测试注入确定性随机源，生产保存实际骰面、修正、DC、结果和规则版本。

RPG Companion 的 Encounter、Randomized Plot、Natural Plot 和正式骰子功能关闭。兼容版根据后端 Tracker 元数据自动切换面板：纯剧情模式隐藏 AC、先攻、法术位和战斗组件；D&D 模式显示后端提供的角色数据、最近骰点和战斗摘要，但不执行结算。

## 12. RPG Companion 兼容契约

### 12.1 工作模式

兼容版使用 Together 模式，关闭 Separate、External API、Auto Update 和 History Persistence。一次主请求完成剧情、状态提交和 Tracker 投影，不产生第二个会被误认作剧情回合的 API 请求。

Presenter 在 Tracker 中附带只读规则元数据：

```json
{
  "rules": {
    "mode": "narrative",
    "enabled": false,
    "version": null
  }
}
```

兼容版按该元数据选择 Narrative、D&D 或 Custom 面板，不依赖用户手动切换 RPG Companion Preset。规则元数据只负责显示；前端修改它不能改变后端 Campaign 配置。

### 12.2 输出格式

```json
{
  "rules": {
    "mode": "narrative",
    "enabled": false,
    "version": null
  },
  "userStats": {
    "stats": [],
    "status": {},
    "skills": [],
    "inventory": {},
    "quests": {},
    "attributes": []
  },
  "infoBox": {},
  "characters": [
    {
      "name": "艾琳",
      "details": {},
      "relationship": {"status": "Ally"},
      "attributes": [
        {
          "key": "alchemy",
          "label": "炼金术",
          "category": "skill",
          "type": "number",
          "value": 35,
          "max": 100,
          "display": "bar"
        }
      ]
    }
  ]
}
```

原版字段保持兼容。兼容版解析器额外保留根级 `rules`，新增 `attributes` 由通用渲染器按 category 分组，并按 display 选择控件。未知显示类型退化为只读文本，不丢弃数据。

### 12.3 只读权威模式

兼容版关闭 Tracker 值的 `contenteditable` 写入，保留折叠、主题、布局和历史查看。用户通过会话命令修改权威状态。面板若解析失败，只显示过期警告；后端状态不回滚、不从面板缓存反向覆盖。

### 12.4 代码块清理

原版会注册过宽的 code-block 清理正则。兼容版把规则收窄为只删除满足统一 Tracker 根键的 JSON block，不能隐藏普通剧情中的任意代码块。

## 13. 持久化模型

SQLite 至少包含：

```text
campaigns
branches
turns
entities
entity_aliases
facts
attribute_definitions
attribute_values
relationships
inventory_entries
quests
trait_events
development_arcs
memory_events
memory_summaries
pending_proposals
dice_rolls
state_snapshots
audit_events
schema_migrations
```

每次状态修改执行单事务：

1. 读取并锁定预期 Campaign、branch 和 state version。
2. 校验候选状态与规则不变量。
3. 写属性、事实、关系、事件和判定。
4. 写 turn 和必要快照。
5. 增加 state version。
6. 提交事务。
7. 事务提交后才生成对外 Tracker 响应。

SQLite 启用 WAL、外键和启动完整性检查。备份使用显式文件目标和轮换策略；导出包含 Schema 版本、Campaign 数据、事件、快照和配置，不包含 API Key。

## 14. Swipe、编辑和分支

每回合保存 `turn_id`、`parent_turn_id`、`branch_id`、`state_before_version`、`state_after_version`、规范化响应哈希和选择历史。

SillyTavern 不保证把稳定 chat/swipe ID 放入标准 Chat Completions 请求，因此后端使用固定 `campaign_id` 加规范化可见历史哈希识别当前父回合。哈希排除 Tracker JSON，只计算清理后的剧情正文。无法唯一识别时不继续写入，返回可诊断的分支选择错误。

新 swipe 从目标回复之前的父状态创建新 branch。切换已有 swipe 时，下一请求按所选历史恢复对应 branch。消息编辑和删除通过可见历史的最长已知前缀寻找最近有效父状态，后续旧回合标记为脱离当前分支但不物理删除。互相矛盾的分支不自动合并。

## 15. API 与配置

SillyTavern 使用 Custom OpenAI Compatible Chat Completion：

```text
Base URL: http://127.0.0.1:8000/v1
```

Custom Body 为每个聊天配置稳定 Campaign：

```json
{
  "campaign_id": "campaign-001"
}
```

Custom Body 不携带规则启停字段。后端从 SQLite Campaign 配置读取 `rules.mode`、`rules.enabled` 和固定版本。

后端提供至少：

- `GET /v1/models`：连接探测。
- `POST /v1/chat/completions`：主游戏回合和只读查询。
- `GET /health`：数据库、迁移和模型连接诊断。
- 本地管理/导出接口使用独立路径，不伪装为聊天回合。

RPG Companion 的合成 Tracker 指令由内容签名和结构共同识别。普通用户消息即使包含相似字样也不能被静默丢弃；Normalizer 必须保留原始请求审计和可解释分类结果。

## 16. 失败与恢复

- LLM 结构化输出无效时允许一次格式纠正；仍无效则不提交状态。
- 任一状态、规则、审计或快照写入失败时回滚整个事务。
- 每个变更携带 expected state version；版本不匹配返回陈旧请求错误。
- 待确认提案绑定 Campaign、branch 和 base version；确认时重新校验。
- Narrator 剧情与权威规则结果矛盾时重写剧情，不能修改已计算骰点迎合文本。
- RPG Companion 解析失败不影响后端权威状态；下一次有效 Tracker 重新同步显示。
- Schema 迁移失败时后端进入只读诊断模式，并允许导出原始数据。
- 最近有效快照和 JSONL 审计用于恢复，恢复操作生成新的审计事件，不篡改旧记录。

## 17. 测试策略与验收标准

### 17.1 单元测试

- 动态属性类型、范围、别名、冲突与显示映射。
- Audience 投影不泄漏隐藏字段。
- 人格变化上限、重复证据和阶段重建。
- D&D 2024 / 5.5e 骰子、行动经济、Weapon Mastery、法术位、资源和状态不变量。
- 事务状态机、版本检查、哈希规范化和检索排序。

### 17.2 集成测试

- 中文会话新增人物、技能、标签、隐藏属性和关系维度。
- 显式修改直接提交；剧情推断只创建提案。
- SQLite 失败、陈旧版本和无效 LLM 输出完整回滚。
- 新建、切换、编辑和删除 swipe 后恢复正确状态。
- RPG Companion 兼容版解析、动态渲染和 per-swipe 恢复。
- 导出、重启、迁移和备份恢复保持相同权威状态。
- Narrative Campaign 不调用 D&D 规则，不要求角色具备 D&D 数据。
- 不同 Campaign 可以同时使用 Narrative、D&D 2024 和 Custom 模式，互不影响。
- D&D Campaign 固定 `dnd-2024` 规则版本；升级勘误不改变旧回合结果。
- 启用 D&D 前补全角色数据并创建快照；关闭后停止结算但保留全部 D&D 状态。
- 活动战斗中未经确认不能关闭规则；暂停和恢复使用相同战斗快照。

### 17.3 长时间测试

模拟至少一万回合，验证：

- 早期关键身份、承诺和人格转折仍可检索。
- 当前 Prompt 不随原始聊天总长度线性增长。
- 重复事件不无限复制。
- 摘要可从原始事件重建。
- 查询和提交时间保持在本地交互可接受范围。

### 17.4 发布验收

1. 对话新增人物后，下一回复自动出现在 RPG Companion。
2. 对话新增技能后，自动出现对应动态控件。
3. 重启 LangGraph 和 SillyTavern 后状态一致。
4. 隐藏属性不进入 Narrator Prompt 和玩家面板。
5. 人格变化可查询以前、现在和原因。
6. swipe 分支互不污染。
7. 非法数值、错误类型和歧义人物不错误写入。
8. 数据库失败不会留下部分更新。
9. Tracker 解析失败不会覆盖后端状态。
10. 成人亲密属性仅对明确成年人物启用，且不能替代具体场景中的当次决定。
11. 骰点、战斗和资源变化都有不可变审计。
12. 一万回合模拟仍能找回早期高重要度事件。
13. 新 Campaign 默认 Narrative，不产生 D&D 骰点或资源结算。
14. 同一 API 下不同 Campaign 可以分别启用和禁用 D&D。
15. 关闭 D&D 后角色卡和战斗记录仍可恢复，RPG Companion 自动隐藏 D&D 面板。

## 18. 实施顺序约束

后续实施计划必须按以下依赖顺序拆分：

1. 独立 Python 领域模型、SQLite Schema、事务与版本。
2. 动态属性、Audience、事件和记忆检索。
3. Campaign 规则配置、确定性规则、骰子与战斗事务。
4. LangGraph 回合编排和 OpenAI 兼容 API。
5. RPG Companion Presenter 与兼容版动态渲染。
6. swipe、编辑、删除和长期模拟。
7. 导出、备份、迁移、诊断和真实 SillyTavern E2E。

每一阶段先写行为测试，再实现最小能力。不得在数据库事务、Audience 隔离和分支正确性尚未验证时提前扩展复杂 UI 或高级 Agent 数量。
