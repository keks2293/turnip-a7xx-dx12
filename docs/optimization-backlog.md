# Backlog улучшений (вне текущего milestone)

Побочные наблюдения за время работы над DGC. Не относится к текущей задаче —
делать отдельными шагами, когда дойдём. Формат: `[сфера] что — почему/где`.

## DGC

1. **Stream BO: suballocator вместо per-Execute alloc.** Сейчас каждый
   `vkCmdExecuteGeneratedCommandsEXT` аллоцирует/мапит/заполняет NOP'ами
   stream-BO (mmap-чирк на каждый Execute; Starfield — десятки Execute в
   кадр). Паттерн: `vis_stream_suballocator` на device / sub_cs на cmd buffer
   (IB-entry ссылается на `(bo, offset, size)` — субаллокацию можно
   референсить тем же `tu_cs_entry`). До M2.5 (прогон игры), после того как
   корректность подтверждена стендами.
2. **Убрать M2.0-спайк после M2.5.** `dgc_probe_*` (шейдеры, DGC_DUMP-выводы,
   dlsym-хуки `tu_dgc_probe_dispatch` для /tmp/opencode/dgcprobe) — debug-канал
   спайка; после валидации игрой драйверу не нужен. Вынести отдельным
   commit'ом (чтобы diff M2.x оставался чистым).
3. **`nir_phi_builder` vs ручные phi.** В этой версии mesa builder-API
   `nir_phi(b, values, at)` удалён; M2.3 строит loop-phi через
   `nir_phi_instr_create/add_src` вручную (см. `dgc_phi` в
   tu_cmd_buffer.cc). Если в M2.4+ добавим ещё лупы/накопители — рассмотреть
   общий хелпер для DGC-шейдеров.
4. **Барьер: polling-маркер вместо `WAIT_FOR_IDLE`.** М2.0-замер: минимум для
   FRESH — `CACHE_CLEAN + WFI + WFM` (4.1–8.4 µs/dispatch; состав и почему
   три — §6 dgc-plan.md). WFI — глобальный wait по всему GPU (и чужие
   очереди), в Starfield это риск. Вариант: шейдер в конце пишет const
   tail-маркер после последнего пакета стрима, CP исполняет
   `CLEAN + WFM + CP_WAIT_REG_MEM (поллинг маркера) + IB` — WFI убирается,
   WFM остаётся (PFE-префетч IB-пакета, §6 R1b). A/B-проверка через
   M2.0-спайк (ножки `DGC_FL`/`DGC_NO_WFM` уже есть, поллинг — дописать).
   После подтверждения корректности probe3/4 — в M2.4+ или отдельным шагом.
5. **NOP-презаполнение stream-BO.** В M2.3 `count == max` всегда (count-адрес —
   M2.4), и шейдер покрывает 100% стрима (цикл `i < count`, `count = max`),
   т.е. полный CPU-memset перед диспатчем избыточен — убрать вместе с
   suballocator'ом (п. 1, тот же участок кода). В M2.4 (`count < max`) хвост
   `[count, max)` кто fill'ит: CPU (только хвост, mmap остаётся) или GPU
   (шейдер пишет константу `0x70100000` — memset исчезает целиком для мелких
   max_count). Решить при M2.4.
6. **M2.4 DISPATCH: шаблон `mem_to_reg`, шейдер на размеры не нужен.**
   Indirect-dispatch путь turnip (`tu_dispatch`, ~9892–9900):
   `mem_to_reg SP_CS_NDRANGE_1` + `CP_REG_RMW`/`scratch_write` +
   `CP_RUN_OPENCL` — CP читает размеры dispatch'а прямо из sequence buffer.
   DISPATCH-токен можно выдать по этому паттерну целиком; шейдер нужен
   только для пакетов, которые CP из памяти не соберёт. Сверить на M2.4-стенде.
7. **Многопоточный preprocess-dispatch** вместо 1×1×1: на большой count
   (макс. max_count) — workgroup/поток на последовательность. M2.3: count
   известен в момент записи → диспатчить `max_count` групп. M2.4 (count из
   GPU-памяти): indirect-диспатч или двухфазный. Низкий приоритет: у Starfield
   max_count маленький, ALU ядра не в бутылке.

## Драйвер (общее)

8. **Опечатка:** `tu_cs_init(..., "prechain draw epiligoue cs")`
   (tu_cmd_buffer.cc, ~4152) — «epiligoue» → «epilogue». Косметика.
9. **`tu_CmdPushConstants2KHR` не валидирует offset+size** против
   `MAX_PUSH_CONSTANTS_SIZE` (просто memcpy в `cmd->push_constants`).
   Утекает за пределы массива при большем offset+size от приложения.
   Чужой код — трогать только отдельным коммитом.
