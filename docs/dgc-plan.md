# План: DGC в turnip (GPU-путь), M2–M4

Контекст и факты — `docs/dgc-analysis.md` (сокращённо «разбор»). Ветка работ:
`starfield-dgc` (turnip-a7xx-dx12), worktree mesa: `build/mesa-rp6-dgc`
(ветка `dgc-starfield`, стек патчей 0001–0012).

## 0. Цель и рамки v1

**Цель:** включить в turnip `VK_EXT_device_generated_commands` так, чтобы
vkd3d-путь Starfield (`[CONSTANT, DRAW_INDEXED]` и `[CONSTANT, DISPATCH]`,
ByteStride=32, N до десятков) дошёл до GPU — игра рисует геометрию.

**В v1 входит:** фича/свойства экстеншена; layout из токенов
PUSH_CONSTANT/PUSH_DATA + SEQUENCE_INDEX + DRAW/DRAW_INDEXED/DISPATCH
(включая count-варианты); preprocess-шейдер, пишущий наш PM4-стрим;
исполнение через dispatch → барьер → `CP_INDIRECT_BUFFER`.

**В v1 не входит (в сигнатурах Starfield отсутствуют):** дескриптор-токены
(CBV/SRV/UAV/BIND, `INDIRECT_BINDABLE`), индексный/вершинный буферы из
списка, mesh/RT, execution set (смена пайплайна per-sequence — пайплайн
фиксирован на Execute, vkd3d обновляет его заранее). На чужих токенах —
громкий отказ при создании layout, не тихий UB.

**vkd3d не трогаем:** нативный DGC-путь там уже есть, сам включится при
наличии фичи (проверка — маркеры §5).

## 1. Архитектура (выбрано)

**Экстеншен и свойства**

- `deviceGeneratedCommands = VK_TRUE`; `dynamicGeneratedPipelineLayout`
  остаётся `VK_FALSE` (vkd3d его и не просит).
- `supportedIndirectCommandsShaderStages = GRAPHICS | COMPUTE`
  (+ `MESH | TASK`, если turnip включает `meshShader` — проверить, иначе
  vkd3d отключит фичу, разбор §1). Отказ по неизвестным токенам — на создании
  layout.
- Лимиты: `maxIndirectCommandsTokenCount ≥ 8`, `maxIndirectSequenceCount ≥
  1024` (Starfield: 2 токена, десятки sequence), `maxIndirectCommandsTokenStride ≥ 64`.
- Точки входа: `vkCreateIndirectCommandsLayoutEXT`,
  `vkDestroyIndirectCommandsLayoutEXT`, `vkGetGeneratedCommandsMemoryRequirementsEXT`,
  `vkCmdExecuteGeneratedCommandsEXT` (основной), `vkCmdPreprocessGeneratedCommandsEXT`
  — **no-op** (см. ниже).
- Зависимости экстеншена закрыты: maintenance5 = true (tu_device.cc:614),
  BDA = true (552), apiVersion = 1.4 (118).

**Preprocess = no-op, работа целиком в Execute.** vkd3d может позвать
preprocess даже в APPLICATION_CALL-режиме (разбор §2), причём в отдельный
command buffer. Упрощение v1: `vkCmdPreprocessGeneratedCommandsEXT` ничего не
делает, а `vkCmdExecuteGeneratedCommandsEXT` **игнорирует `isPreprocessed`**
и всегда эмитит dispatch + барьер + IB. Это корректно: наш Execute не
опирается ни на какие записи preprocess-буфера, лишних барьеров в vkd3d он не
ломает, а гонки «preprocess-буфер против нашего dispatch» не возникает, потому
что пишем в него только мы, в Execute.

**Layout-дескриптор.** При `vkCreateIndirectCommandsLayoutEXT` разбираем
токены в компактный дескриптор (тип действия, смещения в 32-байтной
последовательности, stride, число sequence). Дескриптор кладётся в BO (на
Execute — в params шейдера): один универсальный шейдер на все layout'ы, без
рантайм-компиляции (модель ANV: `layout_addr` в params).

**Препроцесс-шейдер** — NIR, собранный в C по существующему turnip-прецеденту
внутренних шейдеров: `build_*_shader()` + `compile_shader()`
(tu_clear_blit.cc:926 — NIR → `ir3_finalize_nir` → `ir3_shader_from_nir` →
бинар в `global->shaders` c `global_shader_va`), но вариант
`MESA_SHADER_COMPUTE`. Порт `dgc.cl` (ANV) не берём: в turnip нет инфраструктуры
clang→SPIR-V для внутренних шейдеров, а NIR-in-C уже используется.

Логика шейдера (по мотивам `lvp_execute.c:4556-4706` — семантика токенов):

```
вход: layout-дескриптор, indirectAddress (буфер игры),
      sequenceCountAddress (count, если есть), preprocessAddress/Size
для i в [0, count):                     // count читает сам шейдер — GPU-written ок
  seq = indirectAddress + i * stride
  последовательность токенов layout'а:
    PUSH_CONSTANT/PUSH_DATA -> CP_MEM_WRITE(адрес констант-слота_i, данные из seq)
                              + CP_WAIT_MEM_WRITES
                              + SS6-пролог (адрес слота_i)          <-- вопрос §8 разбора
    DRAW_INDEXED            -> CP_DRAW_INDIRECT_MULTI, args = seq + action_offset
    DISPATCH                -> CP_EXEC_CS_INDIRECT,   args = seq + action_offset
```

Пайплайн/дескрипторы/вершинные буферы не трогаем: состояние уже выставлено
до Execute (vkd3d обновляет пайплайн сам), регистры переживают вложенный IB —
так и работает существующий draw-in-IB.

**Исполнение** в `vkCmdExecuteGeneratedCommandsEXT`:

1. params (дескриптор + адреса) в BO;
2. dispatch preprocess-ядра: `CP_EXEC_CS_INDIRECT` с CPU-заполненными 4
   dwords (ядро в `global_shader_va`) — в `cmd->cs` (там же ходят compute-диспатчи);
3. барьер «шейдерная запись → чтение из IB» (опора — существующая семантика
   `INDIRECT_COMMAND_READ`, вопрос §8.2 разбора);
4. `CP_INDIRECT_BUFFER(preprocessAddress)` — в `draw_cs`, дальше по обычному
   draw-потоку.

## 2. Этапы

**M2.0 — спайк GO/NO-GO (делается первым).** Внутренний compute-шейдер в
turnip: собрать минимальное ядро (записать константу в буфер), выгрузить в
`global->shaders`, задиспатчить из cmdbuf, барьер, прочитать результат (тем же
стендом `/tmp/opencode/dgcprobe/`, что M1, + GPU-copy для чтения).
Критерий GO: запись шейдера видна после IB. Отсекает риски R3–R4 до
вложений в остальное.

**M2.1 — экстеншен.** Фича/свойства/лимиты/точки входа (заглушки,
возвращают успех). Проверка: в логе Starfield исчезает
`Not all relevant pipeline stages ... Skipping`.

**M2.2 — layout.** Разбор токенов в дескриптор + валидация v1-объёма
(громкий отказ). `vkGetGeneratedCommandsMemoryRequirementsEXT` — размер под
стрим с запасом (на sequence ~64 dword'а: пролог + MEM_WRITE(≤8) + WAIT +
пакет драва).

**M2.3 — шейдер: константы + draw.** Ядро + params + dispatch + барьер +
IB. Проверка — расширенный стенд: C-тест без vkd3d (создать layout
`[CONSTANT, DRAW_INDEXED]`, заполнить последовательность, Execute →
треугольник, у которого UBO/константа приходит из последовательности; тот же
SIGSEGV-backtrace и dbg-чекпоинты).

**M2.4 — dispatch + count.** Второй тип действия (`CP_EXEC_CS_INDIRECT`),
count-варианты (`*_COUNT`, `sequenceCountAddress`).

**M2.5 — интеграция с vkd3d.** Прогон Starfield: в логе нет `Skipping` и
`DGC-skip`, появляется `EXECUTE_INDIRECT_TEMPLATE`; продублировать
логирование `require_patch` (разбор §8.6).

**M2.6 — регресс.** Прогоны RE4 (`scripts/run-game.sh patched`) и WNO
(`scripts/run-wtno.sh`) — DGC не должен ломать обычный путь; сборка через
podman как обычно.

**M3 — прогон Starfield, таймер 600 с:**
`scripts/run-starfield.sh patched 600`. Критерий — **игрок смотрит на
геометрию** (визуальная проверка, за пользователем); лог без крашей (RC 0),
маркеры §5 на месте. Замер кадрового времени — по возможности здесь же
(базовый ориентир для M4).

**M4 — батчинг и ship.**
- Батчинг: минимум dispatch/барьеров и CPU-wait вокруг Execute (урок upstream
  9840cbf; vkd3d уже умеет batch'ить несколько Execute в один вызов —
  `d3d12_command_list_flush_dgc_batch` — наш вклад: не добавлять лишних
  flush'ей, один params-BO на кадр и т.п.). Отдельный вопрос — не копить
  строку dispatch→barrier→IB там, где можно объединить.
- Патч **0013** в `patches/`, запись в `PATCHES.md`, обновление README и
  разбора (статус M2–M4), cleanup: судьба M1-probe — под явным флагом или
  удалить.
- Пуш — по команде («Push»).

## 3. Риски

| # | Риск | Как проверяем |
|---|---|---|
| R1 | барьер «шейдерная запись → чтение из IB» на a740 (M1 доказал только `CP_MEM_WRITE`) | спайк M2.0: запись из ядра → IB-чтение; дальше держим семантику `INDIRECT_COMMAND_READ` |
| R2 | SS6-пролог per-sequence: точный dword-шаблон из `tu_emit_consts`/`tu6_draw_common` не снят | M2.3: выписать шаблон и сравнить с тем, что CPU эмитит в обычном draw (дамп через существующий rd-dump/cmdstream-дамп) |
| R3 | порядок `cmd->cs` (dispatch) и `draw_cs` (IB): где flush-точки, как не получить гонку потоков | M2.0/M2.3: на стенде смотрим фактический порядок пакетов |
| R4 | внутренний compute-шейдер в turnip — первый; слоты/размер `global->shaders` (assert в `compile_shader`) | M2.0: считаем слоты, при нехватке — отдельный BO вместо `tu6_global` |
| R5 | 30–60 dispatch+barrier на кадр в единственной полосе (разбор §9) — риск для кадрового времени | M3/M4: замер fps до/после; если упирается — оптимизация в M4, multi-queue тема отдельная |
| R6 | count/predication: `sequenceCountAddress` может писать GPU | шейдер читает count сам (это и есть причина GPU-пути); проверка на M2.4 стенде с count-буфером, заполненным GPU |
| R7 | гейт стадий: vkd3d требует `ALL_GRAPHICS\|COMPUTE` (+`MESH\|TASK` при `meshShader`) | M2.1: лог Starfield, смотрим что `Skipping` ушёл |
| R8 | чужие токены/сигнатуры (другие игры) | M2.2: валидация с явным отказом |

## 4. Методы проверки

- Стенд `/tmp/opencode/dgcprobe/` (probe.c + glsl-шейдеры + build.sh, podman,
  gcc/glslang) — расширяется под DGC-вызовы (M2.3). Драйвер — только через
  `VK_DRIVER_FILES=build/out/freedreno_icd.json`.
- Логи: маркеры vkd3d (`Skipping`, `DGC-skip`, `EXECUTE_INDIRECT_TEMPLATE`),
  свои лог-точки в turnip по `TU_DEBUG`-флагу (по аналогии с `tu_dgc_probe`).
- Игра: `scripts/run-starfield.sh [stock|patched] [timeout]`, **следующий
  прогон — таймер 600 с**; проверка геометрии визуальная, за игроком.
- Сборка: podman `localhost/mesa-build-fedora44-full`, команда из M0.

## 5. Связанные артефакты

- Разбор: `docs/dgc-analysis.md` (дамп сигнатур, реализации, решение, §9 —
  многополосность).
- Логи: `results/starfield/starfield-patched-20261004-*.log`
  (дамп-лог 234103 — основной), скрипт `scripts/run-starfield.sh`.
- Fork vkd3d: `keks2293/vkd3d-proton`, ветки `starfield`, `starfield-cmdsig-debug`.
- Worktree mesa: `build/mesa-rp6-dgc` (не трогать `git checkout` в mesa-rp6).
- M1-probe: `src/freedreno/vulkan/tu_dgc_probe.cc` (worktree, не закоммичен).
