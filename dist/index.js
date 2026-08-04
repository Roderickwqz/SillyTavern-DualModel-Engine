// src/index.js
async function bootstrap() {
  return { name: "dualModelEngine" };
}
if (typeof document !== "undefined") {
  void bootstrap();
}
export {
  bootstrap
};
