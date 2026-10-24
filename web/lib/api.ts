const BASE = `${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/api`;

export class ApiError extends Error {
  constructor(public status: number, public code: string) { super(code); }
}

export async function api<T>(path: string, body?: unknown, timeoutMs = 15000): Promise<T> {
  const ctl = new AbortController();
  const t = setTimeout(() => ctl.abort(), timeoutMs);
  try {
    const res = await fetch(`${BASE}${path}`, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? undefined : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: ctl.signal,
      cache: "no-store",
    });
    if (!res.ok) {
      let code = `http_${res.status}`;
      try { const j = await res.json(); code = j.error ?? j.detail ?? code; } catch { /* 본문 없음 */ }
      throw new ApiError(res.status, String(code));
    }
    return (await res.json()) as T;
  } catch (e) {
    if (e instanceof ApiError) throw e;
    throw new ApiError(0, (e as Error).name === "AbortError" ? "timeout" : "network");
  } finally {
    clearTimeout(t);
  }
}

export function errorMessage(e: unknown): string {
  const err = e instanceof ApiError ? e : new ApiError(0, "network");
  if (err.status === 429) return "요청이 잠시 많았어요. 30초쯤 뒤에 다시 시도해주세요.";
  if (err.status === 413) return "입력한 내용이 너무 길어요. 서술을 조금 줄여주세요.";
  if (err.status === 422) return "보낸 답변 형식이 올바르지 않아요. 새로고침 후 다시 시도해주세요.";
  if (err.status === 404) return "결과를 찾을 수 없어요. 처음부터 다시 검사해주세요.";
  if (err.status === 503) return "게임 목록을 준비 중이에요. 잠시 뒤 다시 시도해주세요.";
  if (err.code === "timeout") return "응답이 늦어지고 있어요. 연결을 확인하고 다시 시도해주세요.";
  if (err.status === 0) return "서버에 연결하지 못했어요. 인터넷 연결을 확인하고 다시 시도해주세요.";
  return "문제가 생겼어요. 잠시 뒤 다시 시도해주세요.";
}
