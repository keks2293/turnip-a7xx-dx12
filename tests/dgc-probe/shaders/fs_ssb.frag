#version 450
/* probe10: FS пишет маркер в SSBO. Срабатывает только если растеризатор
 * (VPC→GRAS) реально сгенерировал фрагменты. Дискриминирует:
 *   SSBO[3]=5a5a0f00 → фрагменты есть → мёртв сегмент PS→RB (write/blend);
 *   SSBO[3]=0        → фрагментов нет → мёртв VPC/GRAS (растеризатор).
 * (v[0..2] занимает VS — см. vs_ssb.vert.) */
layout(location = 0) out vec4 c;
layout(set = 0, binding = 0) buffer B { uint v[]; };
void main() {
   v[3] = 0x5a5a0f00u;
   c = vec4(1.0, 0.0, 1.0, 1.0);
}
