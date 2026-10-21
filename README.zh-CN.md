# Garmin 国内 / 国际手动同步

[English](README.md) | 简体中文

Windows / Python 3.12+ 手动同步工具，无需每天定时。克隆后在仓库目录安装依赖：

```powershell
git clone https://github.com/BrianWChen/garmin-cn-sync.git
cd .\garmin-cn-sync
python -m pip install --target .packages -r requirements.txt
```

## 第一次运行

双击 `preview.cmd`。按提示分别输入 Garmin 国内（CN）与国际（COM）账号、密码和可能出现的验证码。密码隐藏输入，不写入配置。Edge 已登录不等于 Python 脚本已登录，此脚本不读取 Edge Cookie。

确认预览后，双击 `sync.cmd` 执行上传。以后训练结束，等码表 / Zwift 在原账号中出现，再双击 `sync.cmd` 即可。

登录令牌、状态及原始活动文件保存在 `private`，已加入 `.gitignore`。令牌可访问账号，勿上传或分享此目录。令牌过期会要求重新登录。

## 同步范围

| 来源 | 目标 | 内容 |
| --- | --- | --- |
| 国内 Garmin | 国际 Garmin | 最近 30 天缺失的已完成活动 |
| 国际 Garmin | 国内 Garmin | 最近 30 天缺失的已完成活动，包括 Zwift |
| 国际 Garmin | 国内 Garmin | 今天起未来 14 天已安排的结构化训练课程和日期 |
| 国内 Garmin | Intervals.icu | 最近 7 天每日健康数据，需 API Key |

活动双向补齐全部来源，避免依赖不可靠的 Zwift 名称识别。课程以国际端为来源，包括已由 Intervals.icu 同步过去的课程；不复制未安排日期的整个课程库。脚本不改变码表、Zwift、Intervals.icu 的现有连接。

国内课程上传后，需要 Garmin Connect 国内 App / Garmin Express 再同步码表。本脚本未显式发送设备推送指令；若设备日历未出现，请在国内 Connect 手动发送课程到码表。

## 手动命令

在 PowerShell 中：

```powershell
cd .\garmin-cn-sync
python sync_garmin.py                         # 预览，不上传
python sync_garmin.py --apply                 # 活动、名称、课程和健康数据一起同步
python sync_garmin.py --apply --since 2025-01-01  # 补较早漏传的活动
python sync_garmin.py --apply --only activities
python sync_garmin.py --apply --only names     # 只校正已匹配活动的名称
python sync_garmin.py --apply --only workouts --workout-days 28
```

重装依赖：`python -m pip install --target .packages -r requirements.txt`。

## 保留同次训练的两边原始记录（可选）

CN 用 Garmin 记录室内骑行、COM 用 Zwift 记录同次训练时，可加
`--keep-overlapping-originals`，两份记录各留在原账号，不跨区上传，也不改名。
默认同步行为不变。

```powershell
python sync_garmin.py --only activities --keep-overlapping-originals
python sync_garmin.py --apply --only activities --keep-overlapping-originals
sync.cmd --keep-overlapping-originals
```

上传前先检查两边记录。配对条件：开始时间相差不超过 10 分钟、实际起止跨度
重叠达到较短跨度的 90%、记录时长比达到 85%。室内距离不比较。
CN 必须是来源标识为 GARMIN 的室内骑行；COM 必须是 ZWIFT 骑行，或在来源标识
缺失时名称以 Zwift 开头的骑行。其他来源和室外骑行沿用现有副本判断。

预览显示 `KEEP originals` 及判断依据。出现多条候选时显示
`REVIEW ambiguous overlap`，暂停这些候选的上传和改名，需人工核对；较短的
拆段记录可能达不到阈值，不自动合并判断。运行时两边都必须已收到原始记录。
不删除已有副本，不把配对记为已上传；下次不加此参数会恢复普通同步。
健康数据和课程同步不受影响。

## 指定历史数据范围

`--from` / `--to` 指定起止日期，**包含首尾两天**。`--since` / `--until` 保留为同义参数。省略 `--to` 时截止今天，省略 `--from` 时活动回看 30 天、健康数据回看 7 天。开始日期必须不晚于结束日期，结束日期不能晚于今天。

```powershell
python sync_garmin.py --from 2025-01-01 --to 2025-01-31 --only activities
python sync_garmin.py --apply --from 2025-01-01 --to 2025-01-31 --only activities
python sync_garmin.py --apply --from 2025-01-01 --to 2025-01-31 --only wellness
python sync_garmin.py --apply --from 2025-01-01 --to 2025-01-31 --only names
```

双击入口也可通过命令行传入日期：`sync.cmd --from 2025-01-01 --to 2025-01-31` 或 `wellness.cmd --from 2025-01-01 --to 2025-01-31`。历史范围仅作用于活动、名称和健康数据；课程仍由 `--workout-days` 控制未来范围。只补历史时建议指定 `--only activities` 或 `--only wellness`，避免同时处理未来课程。长时间跨度的健康数据按天读取，可能耗时较长，可按月分批执行。

## 重复检查与限制

- 活动名称随活动同步：码表来源以国内名字为准，脚本从国际补传的活动以国际名字为准。已同步活动也会检查名称。来源根据原始上传记录确定，避免双向来回覆盖；没有上传记录的已有配对，国际名称以 Zwift 开头则选国际，否则选国内。只在目标修改名称，下次运行仍会以原来源名称覆盖。

- 活动先比较 UTC 起始时间（3 秒内）、时长（5 秒内）、距离（100 米内），疑似已存在就跳过。可能把极接近的不同活动当作已有活动，需要人工检查；不会删除原活动。
- 活动使用原始 FIT / TCX / GPX 导出上传。上传前记录 pending，防止异常退出后重复上传。响应无法确认时，查询目标活动列表，并等待 2、4、8 秒再次查询（另加网络耗时）。仍未确认会停止；下次仅查询确认 pending，不重新上传。确实失败而目标没有活动时，核对后才能清除对应 pending 状态再尝试。
- 课程比较名称、说明、步骤、目标等完整导入内容并记录跨区 ID。日历按目标课程 ID + 日期去重。
- 课程日历以 COM 为准。某天有变化时，先准备新版课程，再删除 CN 当天已同步的旧日历安排，最后安排 COM 当前课程。同步范围内的重复、取消和改期均会处理；未由脚本管理的 CN 课程保留。删除的是日历安排，旧课程模板可能仍留在课程库。替换前备份至 `private/workout-backups/`，完成后回读核对，中途失败可重跑恢复。
- 不是账号完整克隆：国内与国际间仅同步活动和课程。Body Battery、装备、照片、徽章和训练状态等不在同步范围内。国内睡眠 / HRV 等通过下述专用 wellness 接口写入 Intervals.icu。FIT 导入不保证所有 Garmin 派生指标或第三方转发都会产生。
- 非 Garmin 官方同步工具，服务器接口或认证变化可能导致失效。首次必须检查一条活动及一节课程的实际结果。
- 若报 `Calendar response has no workoutId`，说明实际日历数据格式与当前假设不同；脚本会停止，需要按实际接口格式调整，不能声称课程已同步。
- 运行意外退出且提示锁文件存在，确认没有其他同步进程后删除 `private\sync.lock`。

## 验证状态

使用 garminconnect 0.3.17，已实际验证活动双向补传、名称校正、wellness 写入及逐日回读确认。课程设备端接收仍需在码表核对。所有测试数据均为虚构。29 项离线测试通过，可运行 `python -m unittest discover -s tests -v`。

## 每日健康数据：国内 Garmin → Intervals.icu

使用现有 `INTERVALS_ICU_API_KEY` 环境变量，当前账号由 `/athlete/0` 自动解析；也可指定 `INTERVALS_ICU_ATHLETE_ID`。没有环境变量时，运行 `python sync_garmin.py --configure-intervals`，在本机隐藏输入密钥，保存到已忽略的 `private/intervals.json`。

```powershell
python sync_garmin.py --only wellness                      # 最近 7 天预览
python sync_garmin.py --apply --only wellness              # 最近 7 天同步
python sync_garmin.py --apply --only wellness --since 2025-01-01
python sync_garmin.py --only wellness --since 2025-01-09 --until 2025-01-09
```

双击 `wellness.cmd` 只同步健康数据；配置后原 `sync.cmd` 会连同活动、名字和课程一起同步健康数据。`--since` 同时控制活动和健康数据起始日期；不指定时活动回看 30 天、健康回看 7 天。每次重新比较目标记录，已一致的字段跳过；Garmin 修正的数据会再次更新。

同步字段：静息心率、夜间睡眠秒数（不叠加午睡）、睡眠评分、平均睡眠心率、睡眠平均血氧、整晚平均 HRV（RMSSD）、步数、睡眠平均呼吸频率、测量体重（公斤）、晨起训练准备度、爬楼层数。体重只导入实际测量当天的数据，同一天多次测量取最新一次，克换算为公斤，不向后填充。准备度只取明确标记 `AFTER_WAKEUP_RESET` 的晨起值，缺失时跳过。呼吸频率取睡眠平均，爬楼层数四舍五入为整数。睡眠按 Garmin 的醒来日归档。不是全天心率 / 血氧连续曲线；不导入七日 HRV 平均、最高五分钟 HRV，也不把 Garmin 压力分数写到主观压力字段。

国内是上述字段的来源：会覆盖 Intervals.icu 中同字段的不同值，包括手动录入值；没有源值就不更新，不发送 null。主观疲劳、备注等其他字段不写入。写前原记录保存在 `private/wellness-backups/<athlete>/`，写后回读逐字段核对。今天的步数可能未完整，下次运行会刷新。请避免同时让其他来源反复覆盖同一健康字段。

字段依据：[Intervals.icu wellness API](https://forum.intervals.icu/t/best-way-to-integrate-apple-watch-apple-health-data-into-intervals-icu/5776/12)，[Garmin HRV Status](https://www.garmin.com/en-IE/garmin-technology/health-science/hrv-status/)。

## 版权与许可证

Copyright (c) 2026 BrianWChen。本项目自身代码采用 [MIT 许可证](LICENSE)。
第三方依赖保留各自的许可证；`python-garminconnect` 的作者与授权信息见
[第三方版权声明](THIRD_PARTY_NOTICES.md)。本项目为非官方项目，与 Garmin 或
Intervals.icu 无隶属关系，也未获其背书。

接口来源：[python-garminconnect](https://github.com/cyberjunky/python-garminconnect)。
