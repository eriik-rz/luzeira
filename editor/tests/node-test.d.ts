// Tipos mínimos de node:test y node:assert para comprobar las pruebas con tsc sin
// instalar @types/node (regla del proyecto: nada de dependencias nuevas).
declare module 'node:test' {
  export function test(name: string, fn: () => void | Promise<void>): void
}
declare module 'node:assert/strict' {
  interface Assert {
    (value: unknown, message?: string): asserts value
    ok(value: unknown, message?: string): asserts value
    equal<T>(actual: T, expected: T, message?: string): void
    deepEqual<T>(actual: T, expected: T, message?: string): void
  }
  const assert: Assert
  export default assert
}
declare module 'node:fs' {
  export function readFileSync(path: string | URL, encoding: 'utf-8'): string
  export function readdirSync(path: string | URL): string[]
  export function existsSync(path: string | URL): boolean
}
