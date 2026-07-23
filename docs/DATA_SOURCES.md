# 数据源说明

## 数据选择原则

本项目仅使用公开、可复现、无需特殊账号或资质申请的数据源作为论文主证据。商业 AIS、付费卫星 AIS 或无法被同行获取的数据不作为主实验依据。

## 主数据源：NOAA MarineCadastre AIS 2025

用途：主实验数据集。

官方入口：

- NOAA Vessel Traffic: https://www.coast.noaa.gov/digitalcoast/data/vesseltraffic.html
- NOAA AIS 2025 index: https://coast.noaa.gov/htdata/CMSP/AISDataHandler/2025/index.html
- NOAA InPort 2025 metadata: https://www.fisheries.noaa.gov/inport/item/77594
- AccessAIS tool: https://marinecadastre.gov/accessais/

建议使用方式：

- 先下载 2025 年连续 7 天样本跑通流程。
- 再扩展到连续 14-30 天用于论文主实验。
- 使用研究水域边界裁剪原始 AIS，避免处理全国全量数据。

建议研究水域：

- San Francisco Bay / Port of Oakland approaches
- 备选：New York Harbor、Houston Ship Channel

选择理由：

- 2025 年数据在投稿前已经完整公开，时效性足够支撑 2026 年投稿。
- 数据公开下载，无需账号。
- 日文件格式稳定，适合复现实验。
- 美国主要港口水域交通复杂，适合异常行为和风险热区分析。

重要限制：

- NOAA MarineCadastre AIS 不是实时数据源，不提供 live AIS feed。
- 数据源自美国海岸警卫队陆基 NAIS 接收站，不是全球卫星 AIS。
- 近岸接收覆盖、天线位置、无线电干扰和船载设备误差都会影响记录完整性。
- 论文中应明确数据仅用于科研分析和 coastal/ocean planning 场景，不作执法判定。

## 已执行的亚洲跨源补充运行：Tokyo Bay open-access AIS

用途：检验第二公开 AIS schema 上的流程可执行性与共同输出 schema 生成，不替换 NOAA 主基准。

公开入口：

- Figshare 数据集：https://doi.org/10.6084/m9.figshare.29037401.v2
- 配套论文：https://doi.org/10.3390/geomatics6010010
- 本项目 manifest：`configs/data_manifest_tokyo_bay.yml`

已核验信息：

- 数据许可为 CC BY 4.0，无需账号即可下载。
- 版本 2 的 AIS Parquet 文件为 65,622,524 bytes，MD5 为 `460973e34735cb608289fc3e5438dbcd`。
- 全量包含 6,881,633 条消息，覆盖 2024-07-29 至 2024-10-27。
- 原始字段包括纬度、经度、POSIX 时间、MMSI、自报 SOG、船型和目的港。
- 数据不含原生 COG 和 source-reported heading；本项目只在同一 track segment 内由连续位置形成 ground-track course，segment 首点保持方向缺失，并在结果中保留这一可比性边界。

本项目已执行 2024-08-01 至 2024-08-07 的七天补充运行。详细结果和 NOAA 对照见
`docs/REPRODUCIBILITY.md`。

重要限制：

- 数据由研究者通过 aisstream.io 采集，不是日本政府历史 AIS 档案。
- 不能把不同港口的候选数量直接解释为安全水平高低。
- 该实验只支持 cross-source executability 和 common output-schema generation；不证明阈值迁移、跨港性能泛化或固定阈值无需本地校准。

## 可选第三数据源执行候选：Danish Maritime Authority historical AIS

用途：可选的第三数据源 executability 检查，不属于当前论文必需证据。

官方入口：

- DMA AIS data: https://www.dma.dk/safety-at-sea/navigational-information/ais-data
- Historical AIS data: https://aisdata.ais.dk/

建议使用方式：

- NOAA 与 Tokyo Bay 验证完成后，可再选择丹麦 7 天数据做第三水域验证。
- 推荐区域包括 Great Belt、Øresund 或 Copenhagen approaches。
- 如时间不足，丹麦数据可作为后续工作或补充实验，不影响主论文成立。

选择理由：

- 丹麦水域有高密度航运、狭水道、桥区和港口进出场景。
- 与 NOAA 数据地域和交通结构不同，适合验证方法迁移性。
- 官方页面说明历史 AIS CSV 可免费获取。

重要限制：

- 丹麦数据字段、打包方式和质量控制规则可能与 NOAA 不同。
- 实时在线 AIS 与历史开放下载应严格区分。
- 论文中使用时需说明字段映射与清洗规则差异。

## 第一阶段数据计划

优先执行 NOAA 2025 San Francisco Bay 小样本实验：

1. 选择 2025 年 5 月连续 7 天作为快速验证窗口。
2. 下载日文件，裁剪到 San Francisco Bay 研究区域。
3. 生成数据审计表：记录数、船舶数、船型分布、时间范围、字段缺失率。
4. 生成第一版交通密度图。
5. 评估数据量和计算成本，再扩展到 14-30 天。

暂定快速验证日期：

- 2025-05-01 至 2025-05-07

## 数据引用建议

论文中引用 NOAA AIS 时可参考 NOAA FAQ 给出的格式：

BOEM and NOAA. MarineCadastre.gov. Nationwide Automatic Identification System 2025. Retrieved [retrieval date] from https://marinecadastre.gov/data.

实际投稿前需要根据最终使用的数据层和访问日期统一修订。
