# Starfield и VK_EXT_device_generated_commands: разбор

**Устройство:** Retroid Pocket 6 — QCS8550, Adreno **A740** (gen2), драйвер через
`VK_DRIVER_FILES` (сборка worktree), ядро drm/msm с VM_BIND и PRR
**Прогоны:** 04.10.2026, форк vkd3d-proton `keks2293/vkd3d-proton` (ветки
`starfield`, `starfield-cmdsig-debug`)
**Решение (05.10.2026):** реализация DGC в turnip — **GPU-путь** (см. §6);
план — `docs/dgc-plan.md`.

## 1. Симптом

Starfield под патченым turnip запускается, но не отрисовывает часть геометрии.
Обрыв найден на стороне vkd3d: игры используют `ExecuteIndirect` с
**state-template** сигнатурами (команда draw/dispatch + root constants в одном
аргумент-буфере), а vkd3d-proton исполняет такие вызовы только через
`VK_EXT_device_generated_commands`. Если экстеншен/фича недоступны, вызовы
молча пропускаются.

Цепочка в логе `results/starfield/starfield-patched-20261004-234103.log`:

1. `vkd3d_init_device_caps: Not all relevant pipeline stages are supported by
   EXT_dgc. Skipping.` (7 раз на старте) — vkd3d увидел, что turnip не
   выставляет нужные `supportedIndirectCommandsShaderStages`, и отключил DGC
   (`device.c:2606-2616`, выключение фичи на 2615);
2. `d3d12_command_list_ExecuteIndirect: cmdsig-debug: DGC-skip sig=... execute
   #N (stride=32)` — каждый шаблонный ExecuteIndirect отброшен (145 строк,
   счётчики пишутся каждые 1024 исполнения: у отдельных сигнатур до #19456 за
   76 с окна наблюдения; суммарная ранее измеренная частота ≈1950/с ≈ 30–60
   ExecuteIndirect на кадр).

Снимок гейта DGC из кода vkd3d (проверяется по дампу §3):

- `device.c:115` — экстеншен в списке поддерживаемых;
- `device.c:2606-2608` — обязательные стадии: `ALL_GRAPHICS | COMPUTE`, плюс
  `MESH | TASK`, если включён `meshShader`;
- `device.c:2601` — `dynamicGeneratedPipelineLayout` vkd3d всегда просит
  `VK_FALSE` (нам не нужен);
- лимиты: `maxIndirectCommandsTokenCount` (26807), `maxIndirectSequenceCount`
  (26871), `vkGetGeneratedCommandsMemoryRequirementsEXT` (26879);
- `device.c:9609` — `ExecuteIndirectTier` D3D12 берётся из этой же фичи.

## 2. Как vkd3d устроен вокруг DGC

- Шаблонный путь: `d3d12_command_list_execute_indirect` →
  `if (sig_impl->requires_state_template)` →
  `d3d12_command_list_execute_indirect_state_template_dgc(...,
  VKD3D_DGC_MODE_APPLICATION_CALL, 0, 0)` (`command.c:18553-18562`, сама
  функция с 17975).
- Режимы: `enum vkd3d_dgc_mode` = APPLICATION_CALL / PREPROCESS_ONLY /
  PREPROCESS_AND_EXECUTE / EXECUTE_ONLY (`command.c:17963+`).
- `vkCmdPreprocessGeneratedCommandsEXT` вызывается **не только** в
  PREPROCESS_ONLY: `explicit_preprocess` может включиться и в
  APPLICATION_CALL (`command.c:18068-18076`, вызов на 18345) — «if we had
  indirect barriers earlier in the frame, now might be a good time to split».
  Значит, вход `vkCmdPreprocessGeneratedCommandsEXT` экспортировать обязаны.
- `vkCmdExecuteGeneratedCommandsEXT(isPreprocessed = explicit_preprocess ||
  EXECUTE_ONLY ? TRUE : FALSE)` (`command.c:18371-18373`).
- `require_patch` (несобственный путь с GPU-patch шейдером и своим
  `stream_allocation`) выставляется в `command.c:18138-18158`; если он
  включён, vkd3d пишет `WARN("Template requires patching :(")` (18351).

## 3. Дамп сигнатур Starfield (прогон 234103)

Лог: `results/starfield/starfield-patched-20261004-234103.log` (11443
строки), ветка форка `starfield-cmdsig-debug`.

Всего создано 27 сигнатур: **24 с `state_template=1`** (DGC-шаблоны) и 3
простых (`state_template=0`).

| | кол-во | состав | pipeline_type | action_offset | ByteStride |
|---|---|---|---|---|---|
| шаблоны gfx | 16 | `[CONSTANT(type=5), DRAW_INDEXED(type=1)]` | 1 (графика) | 4 или 8 | 32 |
| шаблоны compute | 8 | `[CONSTANT(type=5), DISPATCH(type=2)]` | 3 (compute) | 4 или 12 | 32 |
| plain | 3 | DRAW / DRAW_INDEXED / DISPATCH | 1,1,3 | — | 16/32/16 |

- `action_offset` — смещение draw/dispatch-действия внутри 32-байтной
  последовательности, то есть на константу приходится 1–3 dword'а
  (4+20 ≤ 32 для draw, 4+16 ≤ 32 для dispatch — арифметика сходится).
- Дескрипторов (CBV/SRV/UAV), индексных буферов, mesh/RT-действий в шаблонах
  **нет**. То есть весь v1-объём — push-константы + draw/dispatch, и
  `INDIRECT_BINDABLE` vkd3d не запрашивает вовсе.
- Номера типов — внутренний enum vkd3d: DRAW=0, DRAW_INDEXED=1, DISPATCH=2,
  VBV=3, IBV=4, CONSTANT=5, CBV=6, SRV=7, UAV=8, RAYS=9, MESH=10, INC=11.

## 4. Где лежат данные последовательностей — ключевой факт

В обычном пути (`require_patch == false`) vkd3d передаёт в
`VkGeneratedCommandsInfoEXT`:

```c
// command.c:18318-18329
generated_ext.indirectAddress = arg_buffer->res.va + arg_buffer_offset;
```

то есть **данные последовательностей (константы и аргументы дравов) лежат в
аргумент-буфере самого приложения** — том же буфере, куда D3D12-приложение
кладёт аргументы `ExecuteIndirect`. Такой буфер в D3D12 принято писать с GPU
(culling-проход), и Vulkan об этом ничем не сообщает. Предупреждающего `WARN
("Template requires patching")` в логе 234103 **нет** (grep = 0) → путь
собственно `arg_buffer`, без CPU-переписывания vkd3d.

Дополнительно `sequenceCountAddress` (`command.c:18307-18316`):

- при custom-predication — буфер предикации vkd3d;
- при count-буфере — VA count-буфера (который тоже может писать GPU).

Вывод: **список и count — GPU-данные**. CPU-транслятор мог бы работать только
через readback-столл, что для нашего стека (одна очередь, запрет CPU-waits)
неприемлемо. Это основной аргумент в пользу GPU-пути (§6).

## 5. Обзор реализаций DGC

| драйвер | где транслируется | объём | ключевые места |
|---|---|---|---|
| **RADV** (AMD) | GPU: NIR-шейдер-preprocess, собирается в C | radv_dgc.c = 3637 стр. | при создании layout — `radv_create_dgc_pipeline()` (radv_dgc.c, из `radv_CreateIndirectCommandsLayoutEXT`); при Execute — `radv_prepare_dgc()` (3262) диспатчит его: `radv_CmdDispatchBase(...)` (3373); вызов из `radv_CmdExecuteGeneratedCommandsEXT` (radv_cmd_buffer.c:14497, prepare на 14546). Статичная преамбула/трейлер — CPU (103-113), IB-чейнинг (115). `dgc_emit(struct dgc_cmdbuf*, count, nir_def**...)` (807) — PM4 внутри шейдера |
| **ANV** (Intel) | GPU: OpenCL-мета-шейдер | dgc.cl = 1024 стр., genX_cmd_dgc.c = 1117 стр. | 4 разные dispatch-функции (не 4 вызова за раз): `preprocess_gfx_sequences` (161), `preprocess_cs_sequences` (359), `postprocess_cs_sequences` (456, только для non-INDIRECT_BINDABLE пайплайнов — дописывает binding table в COMPUTE_WALKER, genX_cmd_dgc.c:909-915), `preprocess_rt_sequences` (625, GFX 12.5+). Ядра в dgc.cl: `libanv_preprocess_gfx/cs/postprocess/rt_generate` (394/739/820/905). Один поток на sequence: `emit_simple_shader_dispatch(&state, info->maxSequenceCount, ...)`. Плюс stage-варианты ядер `DGC_*_COMPUTE/FRAGMENT` (anv_internal_kernels.c:317-374) |
| **NVIDIA** | аппарат preprocess (железо) | — | отсюда в EXT-спеке `preprocessAddress`, `isPreprocessed`, `vkCmdPreprocessGeneratedCommandsEXT` |
| **NVK** (mesa, NVIDIA) | — | — | DGC выключен |
| **lavapipe** | CPU (софт-драйвер, эталон семантики) | lvp_execute.c = 6139 стр. | switch по токенам 4556-4706 (PUSH_CONSTANT/PUSH_DATA, SEQUENCE_INDEX, INDEX/VERTEX_BUFFER, DRAW/DRAW_INDEXED/DISPATCH, *_COUNT, MESH, TRACE_RAYS2), `handle_execute_generated_commands_ext` (4778), `handle_preprocess_generated_commands_ext` (4747) |
| **блоб Snapdragon X** | — | — | DGC в a7xx Vulkan-блоке нет (проверено ранее) |
| **turnip** | — | — | экстеншена нет; общий слой mesa `src/vulkan/runtime/vk_device_generated_commands.{c,h}` = 197+109 стр. — только создание/уничтожение layout (разбор токенов в `pc_layouts`); транслятора нет ни у кого, кроме перечисленных |

Среди **GPU**-драйверов CPU-пуля нет ни у одного: AMD и Intel — шейдер,
NVIDIA — железо, у lavapipe GPU нет вообще (он и всё остальное исполняет на
CPU и служит эталоном интерпретации токенов — готовые «ключи» для написания
нашего транслятора, `lvp_execute.c:4556-4706`).

## 6. Два пути и решение

| | CPU-транслятор | GPU-мета-шейдер (выбран) |
|---|---|---|
| Когда читает список | на записи командного буфера (CPU) | диспатч preprocess-шейдера перед Execute |
| Данные из §4 (GPU-written константы/count) | **не читает** — нужен столл | читает сам, без столлов |
| Стоимость на кадр | ~5–10 КБ копирования, µs (N≤3, 100–200 sequence) | 30–60 dispatch+barrier на кадр в единственной полосе |
| Код | ~200–300 стр. эмита PM4 | шейдер + инфраструктура внутренних compute-шейдеров |
| Прецеденты | только lavapipe (софт) | RADV, ANV (и NV — железо) |

Аргументы в пользу CPU были (µs-ы на записи, простая отладка, N≤3, у
вендоров GPU-путь существует из-за GPU-written списков, а наш — CPU-written
vkd3d-ом) — и **отклонены**: факты §4 (список и count лежат в буфере
приложения), отсутствие у vkd3d гарантий о том, кто пишет буфер, плюс
соответствие вендор-модели и запас на будущее (GPU-written списки, тысячи
sequence, count/predication) — решение: **GPU-путь** (пользователь, 05.10.2026).

Издержки, которые берём на себя: собственная шейдерная инфраструктура в
turnip (см. план), dispatch+barrier на каждый Execute (мерить в M3/M4),
одна очередь — preprocess и рендер идут последовательно в одной полосе (§9),
отладка шейдерного кода.

## 7. Что уже сделано

- **M0**: worktree `build/mesa-rp6-dgc` на ветке `dgc-starfield` (стек
  0001–0012), собран; в turnip-a7xx-dx12 создана ветка `starfield-dgc`
  (текущая).
- **M1 = GO** (спайк): в worktree добавлен `tu_dgc_probe.cc` —
  `vkCmdTuDgcProbeWriteEXT(cmd, VkBuffer, off, value, nwrites)` эмитит
  n×`CP_MEM_WRITE` + `CP_WAIT_MEM_WRITES` в `cmd->draw_cs`; экспорт через
  `src/vulkan/vulkan.sym`. Стенд `/tmp/opencode/dgcprobe/` (fullscreen
  triangle, UBO через dlsym, SIGSEGV-backtrace): **SPIKE PASS** (пиксель R255 —
  шейдер прочитал 1.0f из UBO, записанного `CP_MEM_WRITE` внутри того же IB) и
  **CONTROL PASS** (R0 без probe).
- PM4-карта подтверждена: draw = `CP_DRAW_INDIRECT_MULTI` (args из памяти,
  D3D12-layout, `DST_OFF`/vs_params), dispatch = `CP_EXEC_CS_INDIRECT` (4
  dwords из памяти), константы ≤8 dwords = 1×`CP_MEM_WRITE` +
  `CP_WAIT_MEM_WRITES`. Весь draw-поток turnip идёт через
  `CP_INDIRECT_BUFFER`/`draw_cs` (работает в production), compute-диспатчи — в
  `cmd->cs` (основной поток).
- Форк vkd3d запушен: `starfield` (функциональные правки) и
  `starfield-cmdsig-debug` (логи cmdsig-debug/DGC-skip, дамп §3).
- Актуальные вопросы закрыты ранее: A8XX-гейт, FL-12_1 (упирается в
  `VK_EXT_fragment_shader_interlock`), fp16/denorm.

## 8. Открытые вопросы (закрываются в M2)

1. **SS6-пролог**: turnip биндит push-константы указателем `SS6_INDIRECT`
   (tu_cmd_buffer.cc:7612, `tu_emit_consts` 7848) — генерируемому IB нужно
   выставить этот адрес per-sequence. Нужен точный dword-шаблон того, что CPU
   эмитит перед draw (`tu6_draw_common`, 8408).
2. **Барьер**: видимость «запись шейдером → чтение из IB»
   (`CP_INDIRECT_BUFFER`) на a740. M1 доказал видимость для `CP_MEM_WRITE`
   (движок PM4), шейдерные store'ы — отдельный случай; опора — существующая
   семантика барьера `INDIRECT_COMMAND_READ` в turnip (vkd3d его уже
   выставляет под обычные compute→indirect-дравы).
3. **Порядок потоков**: dispatch preprocess — в `cmd->cs`, draws — в
   `draw_cs`; выяснить, где flush-точки и как выстроить
   dispatch → барьер → IB.
4. **Стадии гейта**: vkd3d требует `ALL_GRAPHICS|COMPUTE` (+`MESH|TASK`,
   если turnip включает `meshShader` — проверить; если включает, придётся
   рекламировать и их, с отказом на создании layout вне v1-объёма).
5. Первый в turnip внутренний **compute**-шейдер: механизм есть
   (`build_*_shader` + `compile_shader`, tu_clear_blit.cc:926, NIR → ir3 →
   `global->shaders` c `global_shader_va`), но все текущие — VS/FS; нужен
   compute-вариант и, возможно, расширение слотов/размера `tu6_global`.
6. `require_patch` для Starfield доказан только отсутствием WARN в логе —
   продублировать логированием в форке при M2.5.

## 9. Почему у turnip одна очередь (многополосность)

Исследовано отдельно (05.10.2026), потому что GPU-путь DGC живёт в единственной
полосе и «вторая очередь» упоминалась как возможный источник прироста.

**Железо.** Adreno имеет **4 аппаратных ringbuffer'а, по одному на уровень
приоритета** (Google Project Zero, «Attacking the Qualcomm Adreno GPU»:
«four different ringbuffers, each used for different GPU priorities»; RB0–3).
На A7xx SQE разделён на BV/BR, есть аппаратная преемпция — CP может
переключаться между полосами, приоритетная полоса прерывает текущую.

**Ядро drm/msm — многополосное.** `struct msm_gpu { struct msm_ringbuffer
*rb[MSM_GPU_MAX_RINGS]; int nr_rings; }`, на каждом кольце свой
`drm_gpu_scheduler`. Кольцо выбирается по приоритету submitqueue:
`ring_nr = prio / NR_SCHED_PRIORITIES`, `sched_prio = ...`
(`msm_gpu.h`, `msm_gpu_convert_priority`). Замер с нашего устройства
(ioctl `MSM_PARAM_PRIORITIES`, adreno_gpu.c:407-409 возвращает
`nr_rings * NR_SCHED_PRIORITIES`): **12 = 4 × 3** (3 = HIGH/NORMAL/LOW,
`gpu_scheduler.h`) → **nr_rings = 4** — все четыре полосы есть.

Но: `struct msm_context.entities[NR_SCHED_PRIORITIES * MSM_GPU_MAX_RINGS]` —
«we create at most **one** drm_sched_entity per-process per-priority-level»
(намеренно, ради FIFO-порядка для GL-контекстов). То есть **два submitqueue
с одинаковым приоритетом в одном процессе сериализуются** — ядро даёт полосы
именно по приоритетам, а не по VkQueue.

**Turnip — одна очередь и одна полоса.**

- `queueCount = 1` с самого скелета (26380b3a9f8, 2019);
- каждый VkQueue создаёт один kernel submitqueue с приоритетом **посередине**:
  `prio = submitqueue_priority_count / 2` = 12/2 = 6 (`tu_knl_drm_msm.cc:126-128`)
  → `ring_nr = 6/3 = 2` — **все очереди turnip живут в кольце 2 из 4**;
- VK-глобальные приоритеты turnip умеет мапить
  (`tu_get_submitqueue_priority`, tu_queue.cc:22, `globalPriorityQuery`) —
  механизм полос доступен, но одной очередью не воспользуешься;
- вторая очередь существует только **эмулируемая**: 57c69c99123 (05.2026,
  MR 41924) — «submissions to it are redirected to the real queue... No
  kernel submitqueue is created» — сделана под Android/skiavk (CTS требует
  ≥2 graphics queues); CI-покрытие только у эмулируемой (76e600a868d).

**Вывод «неправильно ли это».** По спеку — нет: `queueCount=1` легален, а
требование «вторая очередь» закрыто эмуляцией (ровно как у Venus/venus-семьи).
По железу — **недоиспользование**: 4 аппаратные полосы, а все сабмиты turnip
ложатся в одну (ring 2), и даже два реальных VkQueue с одним приоритетом
разделили бы одну sched-entity в ядре. Реальный параллелизм потребовал бы
связки «queueCount ≥ 2 + разные submitqueue-приоритеты» (или правки ядра —
entity на очередь). Это upstream-тема (у Collabora в мае 2026 появился только
фундамент эмуляции), на DGC и на Starfield не влияет: vkd3d держит одну
direct-очередь, отдельного compute-семейства у turnip нет, и вторая полоса
даже при наличии не устранила бы последовательность «dispatch → барьер → IB»
внутри одного вызова Execute. Для нас это фон: если замеры M3/M4 покажут, что
очередь — узкое место, тема выносится отдельно.

### Уровни: что умеет железо, а что драйвер

(Уточнение 05.10.2026 после обсуждения «а вдруг драйвер неполноценный».)

**Первоисточник по железу** — Project Zero, «Attacking the Qualcomm Adreno
GPU» (2020), дамп `/sys/kernel/debug/kgsl/globals` с Pixel 3a:

- **4 кольца** `ringbuffer` по 32768 байт (RB0–3) + прямая цитата:
  «four different ringbuffers, each used for different GPU priorities» —
  совпадает с нашим замером A740 (12 = 4 × 3) → **4 — максимум железа**;
- **аппаратная преемпция**: в scratch — «GPU address of a preemption restore
  buffer … used if a higher priority GPU command interrupts a lower priority
  command»; в globals — **4 × `preemption_desc` (≈2 МБ) + 4 ×
  `perfcounter_save_restore`** — состояние контекста сохраняется в память и
  восстанавливается при вытеснении; контекст-свич делает сам CP
  (`CP_SMMU_TABLE_UPDATE`, GPU сам меняет страничные таблицы);
- пользовательские команды идут **не в кольце, а ветвлением
  `CP_INDIRECT_BUFFER_PFE`** из него — та же индирект-модель, что у нашего
  DGC-пути;
- публичной документации нет (P0: «These operations aren't documented») —
  модель «4 независимых исполнителя» ничем не подтверждается: всё известное
  указывает на **арбитраж 4 приоритетных колец SQE с вытеснением** (QoS),
  а реальная ширина — в BV/BR (stage-параллелизм) и ядрах SP/TP.
  BV/BR — не «2 движка очередей»: это 2 потока одного CP —
  `CP_SET_THREAD_BR = Render`, `CP_SET_THREAD_BV = Visibility`
  (adreno_pm4.xml:2303-2305), оба разбирают **один** поток IB; turnip их
  синхронизирует сам (`WAIT_FOR_BR` в `tu_emit_cache_flush`,
  `tu_cmd_buffer.cc:507-539`, под дрirc `allow_concurrent_binning`).

**Слои по степени полноты:**

| уровень | что делает |
|---|---|
| железо | 4 приоритетных кольца + вытеснение контекстов + BV/BR |
| KGSL (драйвер Qualcomm, эталон «полной» реализации) | все 4 кольца, приоритеты контекстов Android, полная преемпция (те же 4×`preemption_desc`), IB-ветвления — **ничего сверх железа** |
| drm/msm (наше ядро) | `nr_rings=4` (замер), `ring_nr=prio/3` (12 уровней), свой drm_sched на кольцо, `enable_preemption` (A7xx+, auto) — параметр есть в `/sys/module/msm/parameters/` на ядре 7.2.6 → ядро максимум железа уже использует |
| turnip | `queueCount=1`, `prio=6` → кольцо 2/4 — **единственный урезанный слой** |

Вывод: скрытых «4 движков» нет — даже Qualcomm в KGSL выжимает те же
4 кольца как QoS-арбитраж. Turnip мог бы задействовать остальные полосы
только связкой «queueCount ≥ 2 + разные глобальные приоритеты»; чисто
теоретически `VK_KHR_global_priority` на единственной очереди уже сейчас
сдвигает её на другое кольцо, но это одна полоса, просто в другом
приоритете.

**Для сравнения — Intel (ANV)** (`anv_physical_device.c:2684-2797`):
семейства очередей = физические движки (`queueCount = intel_engines_count(RENDER)`);
compute (CCS) и transfer (BCS) семейства экспозачиваются **только на Xe KMD**
— на обычной i915 сознательно нет: «not an obvious win in terms of
performance». Каждая VkQueue = свой контекст/exec queue (`anv_private.h:1869`).
У Intel параллелизм — «разные семейства под разные движки»; у Adreno прямого
аналога нет (один SQE, кольца = приоритеты), а на клиентских Intel
`queueCount=1` — ситуация как у нас.

Открытый вопрос (недокументировано даже для P0): точная модель арбитража SQE
— fetch из одного кольца за раз или возможен перекрытие; оснований утверждать
последнее нет.
