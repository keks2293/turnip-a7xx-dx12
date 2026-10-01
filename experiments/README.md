# Эксперименты (НЕ для коммита)

Патчи из `patches/` ship; эти — нет. Они существуют, чтобы выводы в
`docs/analysis.md` можно было перепроверить, а не принять на веру.

| Патч | Что проверяет | Результат |
|---|---|---|
| `0001-experiment-ubwc-all-formats-compatible-a7xx-gen2.patch` | умеет ли gen2 переинтерпретировать UBWC при смене формата (бит `MUTABLEEN`) | нет: чтение через R32_UINT-вид рассыпается, `H` memreq 0x100000 → 0x102000 |
| `0002-experiment-force-is-mutable.patch` | изоляция: ломает ли именно MUTABLEEN, или «что-то ещё включилось» | ломает именно MUTABLEEN: при неизменной паре форматов `T-tr` 0/262144 → 261120/262144 |
| `0003-experiment-no-sparse-create-refusal.patch` | снимает ли отказ 0004 на create то, из-за чего игра на 12_0 пишет «GPU не подходит под минимальные требования» | прогон на игре, разбор в `docs/analysis.md`, раздел 9 |
| `0004-experiment-force-wzyx-when-mutable.patch` | раскатка хака `fd6_format_table.c:396` на mutable-ветку: держать `WZYX` и при `is_mutable`. Требует 0002 — без него `is_mutable` у списков T нет | **порча от MUTABLEEN целиком в swap**: `T-tr` 261120/262144 → **0/262144**, `S-tr` 16384/16384 → **0/16384** |
| `0005-experiment-ubwc-on-without-mutableen.patch` | UBWC включён для несовместимого списка, а `is_mutable` (то есть `MUTABLEEN`) выставлен в 0 — «бит вреден» или «возможности нет»? | **возможности нет**: `H d1'` даёт тот же мусор байт-в-байт (`0x079d685e…`), что и с битом |
| `experiment-cross-order-list-tiled-ubwc.patch` | оставляет ли tiled+UBWC same-shape кросс-порядковый список `{BGRA8, RGBA8}` (проба S3 в тесте): механическая устойчивость и семантика кросс-вида | механически да (`S3-tr` 0/262144, база EXACT, кросс-вид детерминирован), но поведение меняется: identity → swapped (порядок каналов вида игнорирован) против стока/линей; плюс рассинхрон sparse query/create (`E`/`S2` SUCCESS при `FORMAT_NOT_SUPPORTED`) → **откат**, разбор — `docs/analysis.md`, раздел 16 |

Все ложатся на дерево с патчами 0004–0006, каждый по отдельности, и
откатываются `git apply -R <патч>`. `git checkout` для отката **не годится**:
в `tu_image.cc` лежат незакоммиченные 0005/0006, checkout их уничтожит.
0002 и 0005 сужены до собственного ханка, поэтому apply/-R работают поверх
них. Второй эксперимент существует потому, что первый менял две переменные
сразу (бит MUTABLEEN и состав списка) и сам по себе вывода о причине не давал.

Прогон: `scripts/run-experiment-mutableen.sh` (первый). Для остальных:

```sh
cd $WORK/mesa-rp6
git apply experiments/0002-experiment-force-is-mutable.patch
# пересобрать драйвер, прогнать тест, ожидается T-tr 261120/262144
git apply -R experiments/0002-experiment-force-is-mutable.patch
```

0004 накладывается на 0002 (иначе swap и так `WZYX`, разница нулевая),
0005 — сам по себе. A/B 0002 против 0002+0004 делался на одном бинаре
теста: `results/test-expD-control.log` и `results/test-expD.log`;
0005 — `results/test-expE.log`.

Прогон третьего — на игре, а не на тесте, и требует пересборки драйвера,
поэтому порядок такой: игра закрыта, патч применён, драйвер пересобран,
`scripts/run-game.sh patched 200`, потом `git checkout`. Сборку и игру
одновременно не запускать — память устройства общая с GPU.

Логи — в `results/test-mutableen.log` и `results/test-expC.log`.
