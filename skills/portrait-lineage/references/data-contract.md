# 档案与实验数据

运行数据放在用户选择的工作区。默认目录如下，仓库忽略整个 `portrait-data/`：

```text
portrait-data/
  references/                 用户提供的参考图片
  profiles/<id>.json           真人或虚构角色档案
  profiles/history/            修改前的档案版本，名字检索不扫描此目录
  identity-views/<id>/<v>/     人物参考卡、实际请求与逐角度确认
  runs/<run>/run.json          档案快照、图片哈希、引用顺序、配方
  runs/<run>/candidate-01.prompt.txt
  runs/<run>/candidate-01.png
  runs/<run>/review.json       实际观察与用户评价
  sharing/<draft>/            本地小红书图文草稿、素材版本和公开范围
```

Skill 源码可共享，人物资料默认私有。Git 忽略不等于加密，也不能阻止云备份；如用户要求完全离线，内置云端生图不满足该要求。向生图工具传入图片沿用当前会话授权；其他平台的公开展示另记录范围，准备本地图文不等于已授权上传或发布。

## Profile

以 `assets/profile-template.json` 为起点。所有引用路径相对档案所在目录解析；绝对路径也可用，迁移时需修复。当前脚本只接受本地文件，不会根据 URL 自动下载照片。

| 字段 | 约定 |
| --- | --- |
| schema_version | 当前为 1 |
| id / version | 不变的存储 ID 与正整数版本；改名字、特征或主图后增加版本，历史关系仍引用原版本 |
| display_name / aliases | 当前昵称与用户同意保留的旧名；不要求真实姓名，不以名字做人脸识别 |
| age_years | 当前主图的年龄设定；不知道则 null，兼容旧档案的 age；成长时必须有用户依据 |
| name_review | proposed 或 confirmed，proposed_by、confirmed_by 和实际确认 note |
| guidance_mode | 新档案默认 visual_first；未知特征不强行补齐 |
| kind | `real_person` 或 `fictional_character` |
| status | `draft` 或 `approved`；完整复用需外貌、名字、视图确认，或用户明确跳过视图 |
| permission | 真人 reference_use 与说明；publication 单独记录 status、allowed_reference_ids、allowed_names 和 note |
| review | `confirmed_by: user` 与实际认可内容，不允许助手代签 |
| master_reference_id | 真人的真实主图，或虚构角色已认可主图的 reference ID |
| identity_views | status、reference_ids、approved_reference_ids、confirmed_by、note；状态为 pending、approved、approved_partial、skipped_by_user |
| references | 每张图的唯一 id、路径、role、说明及 selector（图中目标人物位置） |
| traits | outline、brow_eye、nose、mouth_smile 四组 |
| lineage | 真人可为空；生成角色保留来源 ID、版本、run 路径、候选 ID |

图片角色：

- `current_photo`：用户标记为本人近照的真实参考。
- `childhood_photo`：本人儿时真实照片；年龄由用户提供。适合辅助年龄变换，不能覆盖另一来源。
- `generated_candidate`：尚未认可的新角色图，只能保留为草稿或指定编辑底图，不能正式复用。
- `approved_reference`：用户认可的虚构角色主图；不用于给真人凭空增加证据。
- `inferred_view`：模型生成的正侧面辅助视图。用户认可后可按任务需要辅助姿态，不替代真实主图，也不参与真人特征事实证据或旧逐部位配方。

生成视图记录 `views`（如 front / three_quarter / profile）、`source_reference_ids`、`run_path` 和 `unsupported_geometry`。`identity_views.approved_reference_ids` 只含已确认且整张可用的视图文件；整卡中有被否定角度时不把整卡列入。原图角色、认可状态与生成来源分别保存，不通过改 role 抹掉推测性质。

`characters.py list/resolve` 只读当前 profiles 目录，列出或解析精确名字/别名/ID；输出主图和可选的已认可辅助图路径及哈希。它检查认可记录和本地文件，不能检验说话者身份、相貌质量或授权真实性。重名不自动合并，旧档案缺少命名或视图状态时需补实际记录，不能假造认可迁移。该工具不自动调用生图或将所有辅助图塞进请求。

`characters.py rename` 记录明确的取名或改名决定，支持对现有草稿确认暂定名。它保留人物 ID、外貌与视图状态，增加版本，将改动前 JSON 原样备份到 `profiles/history/<原文件名>/vNNNN.json`；历史相对路径仍按原 profiles 目录解释。只有用户同意时才加 `--keep-old-name` 保留旧名，新名及保留别名与其他角色冲突则拒绝。记录确认原话不等于工具认证了用户身份。备份已存在、锁未释放或并发改动时拒绝覆盖，先检查现有数据再处理。

`age_progression.py` 保存每个人的原版本、主图哈希、原年龄与目标年龄，输出请求、`stage-candidate.json` 及评审模板。它不生图、不改档案、不自动认可阶段。新年龄阶段被选中后，由代理按用户决定更新同一 ID 的版本；保存原 lineage，另记成长 run 来源。详细规则见 [成长流程](age-progression.md)。阶段候选不能直接复制到当前 profiles 伪装成已认可角色。

新视觉优先档案记录少数可见关系，未知组可以保留空描述、空 evidence_ids、anchor: false；即使用户确认了整体角色，也不凭空补观察。整体认可不意味着每个五官都有独立可靠证据。文字只帮助说明，不取代主图。

以下完整四组约束只用于旧 `lineage.py validate/prepare`：每组包含 `description`、非空 `evidence_ids`、布尔 `anchor`、可选 `age_adaptation`。记录相对关系和形状，如“眉眼间距偏近，眼裂横向延伸”，避免“标准大眼美女”。写清楚照片是否受微笑、镜头或遮挡影响。不要仅凭外貌宣称某特征具有遗传性。

真人特征必须至少有一张 current_photo 或 childhood_photo 支持，且证据列表不能混入推测视图。虚构角色以 approved_reference 支持特征。每人至少两个锚点分布在不同组，确保两来源都有可选的辨识特征；不足时返回建档补充或用户纠正，不编造锚点。

旧分组脚本所需四组证据不足时不能运行该配方；仍可用视觉优先入口。不能直接用“未知”充当锚点，也不能为了通过旧脚本把推测视图改成事实。第一版没有自动置信度分数，避免把视觉判断包装成校准过的概率。

## Run 和 Review

`prepare` 保存完整已确认档案快照、全部参考文件 SHA256、实际输入清单和配方。哈希用于发现文件变化，不证明照片真实、授权有效或生物身份。输出目录必须是新目录；不会覆盖旧实验。修改配方后另开实验，保留原版本。

图片生成完成后，在 review.json 中记录：候选 ID、图片路径及 SHA256、实际工具标识、原始提示词路径、明确观察到的双方特征、未实现项、年龄感、成人缩小脸问题、用户反馈、是否认可及认可原话。无法获取的模型版本、种子、成本写 null。参考快照含本地路径，应与照片一样私有保存。

视觉验收的单项结构为 target、visible_observation、verdict、uncertainty；verdict 仅在实际看图后填写 achieved / partial / not_achieved / uncertain，默认 null。单角色放入 criteria_checks，家庭图的 facial_checks 另带 child_id、source_person_id、reference_id、region，逐孩子核对双方。photo_style_check 分开记录预期与实际摄影状态；local_edit_check 记录底图、改动目标、实际变化、保持项与非目标变化。提示词和用户转述不能代替助手实际看图的观察，用户复盘另标 source: user_report。

用户确认及命名记录针对具体候选和人物。家庭图认可不替代两个孩子各自的主图与名称确认。生成角色每代均有独立 ID，不以“阿禾的孩子”作为唯一身份键。新角色的 lineage 建议结构：

```json
{
  "sources": [{"id": "a", "version": 1}, {"id": "b", "version": 2}],
  "run_path": "../runs/first/run.json",
  "candidate_id": "candidate-02",
  "retained_observations": ["用户确认鼻尖接近 B"],
  "unrealized_intentions": ["眉眼没有达到配方预期"]
}
```

只记录真实反馈。多代复用不能把配方目标累积成事实，也不计算“第几代还剩多少血统”。删除或撤回资料时按用户指定范围处理原图、派生图、档案和实验快照，避免只删入口而留下副本；任何外部发布另行获得授权。
