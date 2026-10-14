# 素材说明

- `assets/icons/buff_fight_yishang.png`：威威易伤，取自指定的 Buff 图标。
- `assets/icons/skill_tq_2_1_1.png`：无名的真气失衡，取自指定的 Buff 图标（非同名技能目录版本）。
- `assets/icons/skill_qishu_huimaqiang.png`：骑龙回马，取自指定的奇术图标。
- `assets/icons/skill_qishu_kongmingdeng.png`：千灯一月，取自指定的技能图标。

上述四张图均为《燕云十六声》原始图标，仅保留运行所需的 RGBA 像素，重新编码去除元数据；不包含本机原始素材库或其他未使用图片。相关游戏素材权利归原权利人所有。

- `yysls_export/` 的三个 PNG 为《燕云十六声》的技能图标与边框，用于本机识别和浮层显示，相关游戏素材权利归原权利人所有。本项目为非官方工具。
- `tests/fixtures/loss_user_dim.png` 只包含裁剪后的技能图标，没有完整游戏画面、角色名或聊天信息。
- `assets/audio/*_electronic.wav` 为正弦波、谐波和淡入淡出包络合成的电子提示音。
- `assets/audio/dizi_baidu.wav` 与 `loss_baidu.wav` 为预生成的「笛子好了」「打响指了」语音，来源为百度翻译公开朗读接口；运行时播放本地 WAV，不发送网络请求。

仓库公开不代表第三方素材取得了额外再授权；本说明不向游戏图片或第三方语音授予开源许可。

本项目原创代码、文档和配置采用 [非商业源码许可](LICENSE)，不覆盖上述第三方素材，也不改变第三方依赖的许可证。免费分享修改版时，仍须独立遵守相关素材的权利和使用条件。
