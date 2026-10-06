#version 450
/* probe9: VS пишет gl_VertexIndex в SSBO — проверяем, исполняется ли
 * вершинный этап вообще. Если SSBO заполнился — фронт-енд жив до VPC,
 * мёртвый сегмент VGT/GRAS/RB-render. Если пустой — мёртв VFD/dispatch. */
layout(location = 0) in vec2 p;
layout(set = 0, binding = 0) buffer B { uint v[]; };
void main() {
   v[gl_VertexIndex] = 0x5a5a0000u + uint(gl_VertexIndex);
   gl_Position = vec4(p, 0.0, 1.0);
}
