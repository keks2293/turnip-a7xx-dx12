# Свойства `fd_dev_info.props` по суб-поколениям A7XX

Источники:

- `src/freedreno/common/freedreno_devices.py` — блоки `a7xx_gen1`, `a7xx_gen2`, `a7xx_gen3`
- `src/freedreno/common/freedreno_dev_info.h` — типы и комментарии полей
- `src/freedreno/vulkan/`, `src/freedreno/ir3/` — фактическое использование

Привязка устройств (по `freedreno_devices.py`):

| блок | устройства | chip |
|---|---|---|
| `a7xx_gen1` | FD730 | Adreno 730 |
| `a7xx_gen2` | X1-45/FD735, **FD740 + X1-85 (наш)**, FDA32, FD740v3/Quest 3 | Adreno 740 и родня |
| `a7xx_gen3` | FD750, 810/829/830/840/X2-85/X2-90 | Adreno 750+ |

**Как читать «дефолт».** Структура `fd_dev_info` в сгенерированном
`freedreno_devices.h` — designated initializer: поле, которое не задал ни один
`GPUProps`, не печатается и в C равно `0`. Для a7xx список свойств —
`[a7xx_base, a7xx_genX]`, то есть `a6xx_base` **не входит**, а `a7xx_base`
не задаёт ни одного свойства из таблицы ниже. Поэтому дефолт для всех bool —
`False`, а для `max_draw_states` — особый случай (см. заметку после таблицы).

Строки упорядочены по группе появления: `123` → `12.` → `1..` → `.2.` → `.23` → `..3`.

| # | свойство | тип | gen1 | gen2 | gen3 | дефолт | комментарий из `freedreno_dev_info.h` | описание (что делает) |
|---|---|---|---|---|---|---|---|---|
| 1 | `supports_uav_ubwc` | bool | True | True | True | False | Whether UBWC is supported on all UAVs. Prior to this, only readonly or writeonly UAVs could use UBWC and mixing reads and writes was not permitted. | UBWC разрешён на любых UAV; раньше — только на readonly/writeonly, смешивание чтения и записи запрещено. |
| 2 | `enable_tp_ubwc_flag_hint` | bool | True | False | — | False | On a740 TPL1_DBG_ECO_CNTL1.TP_UBWC_FLAG_HINT must be the same between all drivers in the system, somehow having different values affects BLIT_OP_SCALE. We cannot automatically match blob's value, so the best thing we could do is a toggle. | Программировать ли `TPL1_DBG_ECO_CNTL1.TP_UBWC_FLAG_HINT`. Значение должно совпадать у всех драйверов в системе, иначе ломается `BLIT_OP_SCALE`. У **нашего FD740 = False**; True стоит только у X1-45/FD735 и Quest 3. Форс `True` у нас даёт артефакты в RE4 (раздел 15.4 `analysis.md`), 0% в vkmark. |
| 3 | `fs_must_have_non_zero_constlen_quirk` | bool | True | True | — | False | Having zero consts in one FS may corrupt consts in follow up FSs, on such GPUs blob never has zero consts in FS. The mechanism of corruption is unknown. | Запрет на нулевой constlen у фраг-шейдера: ноль констант в одном FS портит константы следующих FS (порча в ir3). |
| 4 | `reading_shading_rate_requires_smask_quirk` | bool | True | True | — | False | A7XX gen1 and gen2 seem to require declaring SAMPLEMASK input for fragment shading rate to be read correctly. This workaround was seen in the prop driver v512.762.12. | Нужно объявлять вход `SAMPLEMASK`, чтобы корректно читать fragment shading rate. Найдено в блобе v512.762.12. |
| 5 | `cs_lock_unlock_quirk` | bool | True | — | — | False | Is lock/unlock sequence needed at end of compute shader? | Перед концом compute-шейдера вставляются `OPC_LOCK` / `OPC_UNLOCK` (`ir3_compiler_nir.c:6194`). |
| 6 | `stsc_duplication_quirk` | bool | — | True | — | False | A7XX / gen7 stsc may need to be done twice for the same range to workaround _something_, observed in blob's disassembly. | Запись STSC (state-table config) для одного диапазона делается дважды. Наблюдение из дизассемблера блоба. |
| 7 | `ubwc_unorm_snorm_int_compatible` | bool | — | True | True | False | Whether the UBWC fast-clear values for snorn, unorm, and int formats are the same. This is the case from a740 onwards. These formats were already otherwise UBWC-compatible, so this means that they are now fully compatible. | Быстрый UBWC clear даёт одно и то же значение для snorm/unorm/int → эти форматы полностью совместимы между собой (с a740). |
| 8 | `has_64b_image_atomics` | bool | — | True | True | False | — | 64-битные атомики в изображениях → фича `EXT_shader_image_int64_atomics` / `EXT_shader_image_atomic_int64` (`tu_device.cc:378,774`, `tu_formats.cc:303`). |
| 9 | `has_64b_ssbo_atomics` | bool | — | True | True | False | — | 64-битные атомики в SSBO → фича `VK_KHR_shader_atomic_int64` (`tu_device.cc:274,516`). |
| 10 | `has_event_write_sample_count` | bool | — | True | True | False | Whether there is CP_EVENT_WRITE7::WRITE_SAMPLE_COUNT | Наличие pkt7 `CP_EVENT_WRITE7::WRITE_SAMPLE_COUNT`. |
| 11 | `has_hw_bin_scaling` | bool | — | True | True | False | a740+ support a per-view list of bin scales in GRAS which can be used to modify the viewport, rather than manually patching it in the driver. | Аппаратный per-view список bin scales в GRAS вместо ручной правки viewport в драйвере. |
| 12 | `has_image_processing` | bool | — | True | True | False | Whether the device supports the image processing opcode | Наличие опкода image processing. |
| 13 | `has_implicit_fragface_fragcoord_ij_linear` | bool | — | True | True | False | Whether GRAS_CL_INTERP_CNTL has FACENESS/CENTERRHW and thus being able to avoid setting ij_linear_sample for FragFace/FragCoord. | `GRAS_CL_INTERP_CNTL` содержит FACENESS/CENTERRHW → не нужно выставлять `ij_linear_sample` для FragFace/FragCoord. |
| 14 | `has_primitive_shading_rate` | bool | — | True | True | False | — | Фича `primitiveFragmentShadingRate` (`VK_EXT_fragment_shading_rate`) — `tu_device.cc:596,1349`. |
| 15 | `has_ray_intersection` | bool | — | True | True | False | Whether the ray_intersection instruction is present. | Наличие инструкции `ray_intersection` (пересечение лучей) — база для ray tracing. |
| 16 | `has_generic_clear` | bool | — | — | True | False | Whether a single clear blit could be used for both sysmem and gmem. | Один clear-blit подходит и для sysmem, и для GMEM → generic clear вместо чип-специфичного. Точки: `tu_clear_blit.cc:4013` (`use_generic_clear_for_image_clear`), `:4030` (event vs cp blit), `:4051` (flush `CCU_INVALIDATE_COLOR\|WAIT_FOR_IDLE`), `:4866` (`tu_clear_attachments_generic`), `tu_cmd_buffer.cc:6690/719` (отдельные clear-ы не эмитятся), `tu_pass.cc:560` (отключается условная load/store), `tu_query_pool.cc:1574` (`CCU_INVALIDATE_DEPTH`). **Включён по умолчанию для gen2 вместе с #17 — патч 0008**; замеры: синтетика −61…−65%, RE4 0%, кадры без порчи (раздел 15 `analysis.md`). |
| 17 | `r8g8_faulty_fast_clear_quirk` | bool | — | — | True | False | Whether r8g8 UBWC fast-clear work correctly. | Обратный флаг: UBWC fast-clear для **R8G8 работает некорректно**, поэтому guard в `use_generic_clear_for_image_clear` запрещает generic clear для `image_is_r8g8` (`tu_clear_blit.cc:3008,4013`). Единственный guard на R8G8. Смысл только в паре с #16: без generic clear условие (`has_generic_clear && !(quirk && image_is_r8g8)`) ложно в любом случае, то есть квирк один — no-op. **В паре включён по умолчанию для gen2 — патч 0008**: пара бесплатна (0% в vkmark и RE4), а без квирка очистки R8G8 generic-путём дают GPU fault (fast-clear + размеры типа 960x540 + GMEM renderpass). |
| 18 | `load_shader_consts_via_preamble` | bool | — | — | True | — | — | Константы шейдера и bindless base addresses грузятся в preamble, а не отдельным draw-state CONST (`tu_cmd_buffer.cc:1827,7530,7548`). |
| 19 | `load_inline_uniforms_via_preamble_ldgk` | bool | — | — | True | — | — | Inline UBO (дескрипторная память) загружается инструкцией `LDGK` из preamble (`tu_shader.cc:1097,1307`, `tu_cmd_buffer.cc:7674,7735`). |
| 20 | `has_gmem_vpc_attr_buf` | bool | — | — | True | False | — | Включает отдельный VPC attribute buffer; эмитится в начале и при смене CCU-состояния (`tu_cmd_buffer.cc:598,739,2332,2450`). |
| 21 | `sysmem_vpc_attr_buf_size` | uint32_t | — | — | 0x20000 | 0 | Size of various in-gmem caches: | Размер VPC attr buf в режиме sysmem (`fd6_gmem_cache.h:92,99`). |
| 22 | `gmem_vpc_attr_buf_size` | uint32_t | — | — | 0xc000 | 0 | — | Размер VPC attr buf в режиме GMEM (`fd6_gmem_cache.h:84,102`); программируется в `VPC_ATTR_BUF_GMEM_SIZE` (`tu_cmd_buffer.cc:607`). |
| 23 | `ubwc_all_formats_compatible` | bool | — | — | True | False | A750+ added a special flag that allows HW to correctly interpret UBWC, including UBWC fast-clear when casting image to a different format permitted by Vulkan. So it's possible to have UBWC enabled for image that has e.g. R32_UINT and R8G8B8A8_UNORM in the mutable formats list. | Флаг a750 разрешает UBWC + fast-clear при касте формата через mutable format list. **На a740 включать нельзя** — ущерб из двух независимых частей (разделы 14.3 и 14.5 `analysis.md`): (1) `vkCreateImage` для MUTABLE+SPARSE начинает отдавать `SUCCESS` с линейным sparse-образом вместо `FORMAT_NOT_SUPPORTED`; (2) переинтерпретация UBWC между форматами не работает в принципе — `H d1'` (R32_UINT-вид поверх RGBA8) даёт мусор `0x079d685e…` **и с `MUTABLEEN`, и без него** (эксперимент 0005, `results/test-expE.log`). Порча же от самого бита `MUTABLEEN` — целиком в `swap` (261120/262144 → 0/262144 после хака 0004, `results/test-expD*.log`), и лечится она только тем, что убирает нанесённый ею же ущерб: ни одного случая, где флаг работал бы лучше base, тест не показал. |
| 24 | `ubwc_coherency_quirk` | bool | — | — | True | False | a750 has a bug where writing and then reading a UBWC-compressed UAV requires flushing UCHE. This is reproducible in many CTS tests, for example dEQP-VK.image.load_store.with_format.2d.*. | a750: после записи в UBWC UAV нужно сбрасывать UCHE перед чтением. Добавлен в `TU_FORCE_PROPS` (патч 0007) и прогнан в комбо — **ничего не меняет**: с `ubwc_all_formats_compatible` лог байт-в-байт совпадает с ним одним (раздел 14.4 `analysis.md`). |
| 25 | `has_compliant_dp4acc` | bool | — | — | True | False | — | `DP4ACC` даёт спецификационное поведение `sdot`/`udot` 4x8 → включаются NIR-опции `has_sdot_4x8[_sat]` (`ir3_compiler.c:406`, `ir3_compiler_nir.c:436`). |
| 26 | `has_abs_bin_mask` | bool | — | — | True | False | Whether CP_SET_BIN_DATA5::ABS_MASK exists | Наличие поля `ABS_MASK` в pkt7 `CP_SET_BIN_DATA5`. |
| 27 | `has_persistent_counter` | bool | — | — | True | False | Whether CP_ALWAYS_ON_COUNTER only resets on device loss rather than on every suspend/resume. | `CP_ALWAYS_ON_COUNTER` сбрасывается только при device loss, а не на каждом suspend/resume → пригоден для autotune-счётчиков. |
| 28 | `gs_vpc_adjacency_quirk` | bool | — | — | True | False | On a750 there is a hardware bug where certain VPC sizes in a GS with an input primitive type that is a triangle with adjacency can hang with a high enough vertex count. | a750: определённые размеры VPC в GS с треугольниками+adjacency зависают при большом числе вершин. |
| 29 | `has_alias_rt` | bool | — | — | True | False | Whether alias.rt is supported. | Поддержка инструкции `alias.rt`. |
| 30 | `has_rt_workaround` | bool | — | — | True | False | a750-specific HW bug workaround for ray tracing | Обход HW-бага ray tracing, специфичного для a750. |
| 31 | `has_sw_fuse` | bool | — | — | True | False | Whether features may be fused off by the SW_FUSE. So far, this is just raytracing. | Возможность отключения фич через SW_FUSE; пока это только raytracing. |
| 32 | `new_control_regs` | bool | — | — | True | False | On a750 the control register layout is rearranged. | На a750 раскладка control registers переставлена. |
| 33 | `max_draw_states` | uint32_t | — | — | 64 | 0 | The amount of valid draw state IDs. | Число допустимых draw state ID. Единственное использование — `tu_autotune.cc:339`: `max_draw_states > TU_DRAW_STATE_AT_WRITE_RP_HASH` включает флаг autotune `PREEMPT_OPTIMIZE`. |

Итого: **gen1 = 5, gen2 = 14, gen3 = 28.**

## Замечания

### `max_draw_states` у a740 равен 0

`max_draw_states = 32` задан только в `a6xx_base` (`freedreno_devices.py:152`).
Устройства a7xx используют список `[a7xx_base, a7xx_genX]` — `a6xx_base` в нём
**нет**, а `a7xx_base` этого поля не задаёт. В сгенерированном
`freedreno_devices.h` поле отсутствует у шести a7xx-записей и присутствует
(`64`) только у `__info26` (gen3).

Следствие: `TU_DRAW_STATE_COUNT = 19 + 13 = 32`, значит
`TU_DRAW_STATE_AT_WRITE_RP_HASH = 33`, и условие `0 > 33` ложно — на a730/a740
`PREEMPT_OPTIMIZE` **выключен по умолчанию**. Включается только
`TU_MAX_DRAW_STATES=64` (патч 0007); на FPS это не влияет (раздел 14 `analysis.md`).

Ранее в разговоре встречалось число 128 — его в коде нет.

### Покрытие `TU_FORCE_PROPS`

`tu_force_props()` (`tu_device.cc:1717`) умеет переопределять 12 флагов:

```
load_shader_consts_via_preamble, load_inline_uniforms_via_preamble_ldgk,
has_generic_clear, has_gmem_vpc_attr_buf, ubwc_all_formats_compatible,
has_compliant_dp4acc, has_persistent_counter, has_abs_bin_mask,
ubwc_coherency_quirk, cs_lock_unlock_quirk, enable_tp_ubwc_flag_hint,
r8g8_faulty_fast_clear_quirk
```

плюс отдельно `TU_MAX_DRAW_STATES` (число). Каждое срабатывание пишет в лог
`TU_FORCE_PROPS: <имя>` — по этим строкам видно, что переменная дошла до
драйвера (проверялось на игровых прогонах, раздел 15.4 `analysis.md`).

- `has_gmem_vpc_attr_buf` при форсе сам подставляет размеры `0x20000`/`0xc000`,
  если их не задали явно (`tu_device.cc:1753`).
- `r8g8_faulty_fast_clear_quirk` добавлен в список, чтобы проверить, не
  снимает ли защита R8G8 выигрыш `has_generic_clear` — не снимает
  (`gc_r8g8 ≈ gc`, раздел 15.2 `analysis.md`).
- `enable_tp_ubwc_flag_hint` при форсе в `True` даёт **артефакты в RE4**
  (раздел 15.4 `analysis.md`); в vkmark и тесте порчи нет.
- `ubwc_all_formats_compatible` и `load_shader_consts_via_preamble` в форсе
  дают порчу (проверено тестом `rp6-vkd3d-sparse-test`).
- `ubwc_coherency_quirk` добавлен в список ради проверки, не уберёт ли он порчу
  от `ubwc_all_formats_compatible` — не уберёт: комбо даёт байт-в-байт тот же
  лог, что и `ubwc_all_formats_compatible` один (раздел 14.4 `analysis.md`).
  Других props в этом пути нет.

### `enable_tp_ubwc_flag_hint`: почему True встречается у других

Сырое `a740_raw_magic_regs` содержит `TPL1_DBG_ECO_CNTL1 = 0x00040724`
(бит18 `TP_UBWC_FLAG_HINT` есть), а проп gen2 его вычищает → в игру идёт
`0x00000724`. История по git:

- `d853443a2ea3` (17.06.2024): *«Most devices with a740 have blob v6xx which
  doesn't have TP_UBWC_FLAG_HINT set. Match them for better compatibility»* —
  проп поставлен в `False` под блоб v6xx.
- `7968b356f8c` (19.09.2024, закрывает `#10316`): чинит A740v3/Quest 3 —
  chip_id не матчился, Quest 3 работал как обычный a740 и портил картинку;
  лечебным указан `RB_DBG_ECO_CNTL = 1`, а hint включён потому, что
  *«Quest 3 ships with blob version 7xx»*.

То есть **один и тот же кремний a740 у разных продуктов разводится по
блобу**: телефоны (v6xx) → `False`, Quest 3 (7xx) → `True`. Значение
`0x00040724` в raw-списке — состояние старого/чужого dump'а, а не поведение
нашего блоба, поэтому «совпадение с блобом» здесь не проверяемо и проп
сделан тумблером. У a730 (`gen1`) raw-значение то же самое, а `True` —
это то же состояние, что у Quest 3.

Для gen3 проп мёртв: в raw-списке FD750 (`# Values from blob v676.0`)
`TPL1_DBG_ECO_CNTL1` вообще отсутствует, то есть регистр не программируется
и значение бита не меняется ни при каком состоянии пропа.

Практика (раздел 15 `analysis.md`): на нашем a740 форс `True` даёт 0% в
vkmark и **артефакты в RE4** — включать нельзя, `False` остаётся верным.

### О чём говорит/не говорит «—»

`—` = свойство не задано в блоке → в C равно `0`/`False`. Это **наблюдение из
блоба Qualcomm для конкретного чипа**, а не деление по поколению: формально
830 в таблице не участвует, а `enable_tp_ubwc_flag_hint` внутри gen2 тоже
различается (True у X1-45 и Quest 3, False у FD740).
