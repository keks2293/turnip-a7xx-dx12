#version 450
/* три цветовые полосы (контроль геометрии): x<170.67 red, <341.34 green, else blue.
 * Проверяются точки (77,128)=0xFF0000FF (281,128)=0xFF00FF00 (435,128)=0xFFFF0000. */
layout(location = 0) out vec4 c;
void main() {
   float x = gl_FragCoord.x;
   if (x < 170.67)
      c = vec4(1.0, 0.0, 0.0, 1.0);
   else if (x < 341.34)
      c = vec4(0.0, 1.0, 0.0, 1.0);
   else
      c = vec4(0.0, 0.0, 1.0, 1.0);
}
