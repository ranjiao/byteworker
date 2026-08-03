# byteworker · digest 细则 —— 飞书白板

> 由 `references/digest-doc.md` 路由到这里。飞书文档正文中的内嵌 whiteboard 是当前文档内容，
> 不是普通“延伸阅读”；默认随正文读取。引用到其它文档中的白板仍走重要依赖闸门。

## 读取与覆盖

- 对正文中的每个 `<whiteboard token="...">`，只用 `lark-whiteboard` 读取结构化节点 JSON。
  不获取、不生成、不查看整体预览图或截图，禁止为 digest 调用 `view_image` 分析白板。
- 结构 JSON 是原始证据：至少保留节点 id/type、文本、坐标、尺寸、父子关系；connector 可取得
  时保留起终点、箭头、转折点和 caption。
- 所有正文白板均成功读取才写 `whiteboards_status: complete`；任一 token 无权限、失败或结果
  截断则写 `partial`，列出缺口。完整性只表示全部结构 JSON 已取得，不表示视觉外观已复核。
- 白板结构 JSON 作为 `kind=whiteboard` component 进入 digest plan，name 固定建议为
  `whiteboard:<token>`，`uid=<token>`，`mode=canonical-json`。

## 理解与证据语义

可以作为结构直接证据：

- 节点显式文本、形状类型和父子分组。
- connector 明确连接的起点、终点和方向。
- 白板中显式标注的系统、角色、接口、流程步骤、时间或状态。

不得作为证据：

- 仅由颜色、远近、对齐、空白或图标外观推测出的分组或语义。
- 无 connector、无父子关系、只靠相对位置推测的调用或依赖方向。
- 结构 JSON 没有显式编码、必须观察渲染图片才能成立的解释；直接标为“未验证”，不做视觉推断。

白板画出完整架构不等于系统已上线；成熟度、覆盖率、SLA、发布时间等仍需正文、评论、数据或其它
一手来源支持。

## 评论锚点

评论 relation 指向当前文档内已摄取 whiteboard 时，用 `parent_token` 对齐对应 component；评论
仍按【主张】/【意图】/【观察】保存。relation 指向外部白板或另一篇文档中的 whiteboard 时，先
登记 token，只有它是重要依赖且用户同意扩展范围后才读取。

## 大小边界

白板数量或节点总量使输入进入“大型输入”时，继续遵守 `references/digest-large.md`；把结构
JSON 落临时文件并交隔离上下文分析，主 Agent保留依赖范围确认。结构 JSON 必须完整进入 bundle，
不得以截图、OCR 或摘要代替。
