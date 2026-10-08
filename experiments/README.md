# Эксперименты (НЕ для коммита)

Патчи из `patches/` ship; эти — нет. Они существуют, чтобы выводы в
`docs/analysis.md` можно было перепроверить, а не принять на веру.

| Патч | Что проверяет | Результат |
|---|---|---|
| `0001-experiment-ubwc-all-formats-compatible-a7xx-gen2.patch` | умеет ли gen2 переинтерпретировать UBWC при смене формата (бит `MUTABLEEN`) | нет: чтение через R32_UINT-вид рассыпается, `H` memreq 0x100000 → 0x102000 |
| `0002-experiment-force-is-mutable.patch` | изоляция: ломает ли именно MUTABLEEN, или «что-то ещё включилось» | ломает именно MUTABLEEN: при неизменной паре форматов `T-tr` 0/262144 → 261120/262144 |

Оба ложатся на дерево с патчами 0004–0006, каждый по отдельности, и откатываются
`git checkout`. Второй эксперимент существует потому, что первый менял две
переменные сразу (бит MUTABLEEN и состав списка) и сам по себе вывода о
причине не давал.

Прогон: `scripts/run-experiment-mutableen.sh` (первый). Для второго:

```sh
cd $WORK/mesa-rp6
git apply experiments/0002-experiment-force-is-mutable.patch
# пересобрать драйвер, прогнать тест, ожидается T-tr 261120/262144
git checkout src/freedreno/vulkan/tu_image.cc
```

Логи — в `results/test-mutableen.log` и `results/test-expC.log`.
