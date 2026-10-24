// 브라우저 저장소는 진행 중 임시 응답과 내 결과 열람 키에만 쓴다. 접근이 막혀도 동작해야 한다.
type Kind = "session" | "local";
function store(kind: Kind): Storage | null {
  try { return kind === "session" ? window.sessionStorage : window.localStorage; } catch { return null; }
}
export function load<T>(kind: Kind, key: string): T | null {
  try { const v = store(kind)?.getItem(key); return v ? (JSON.parse(v) as T) : null; } catch { return null; }
}
export function save(kind: Kind, key: string, value: unknown): void {
  try { store(kind)?.setItem(key, JSON.stringify(value)); } catch { /* 저장 불가 무시 */ }
}
export function remove(kind: Kind, key: string): void {
  try { store(kind)?.removeItem(key); } catch { /* 무시 */ }
}
export const KEYS = { progress: "gembti:progress:v1.2", result: "gembti:result", last: "gembti:last", eval: "gembti:eval" };
