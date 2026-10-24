import { api } from "./api";
import { load, save } from "./storage";

export interface V2Session { session_key: string; owner_key: string }
export interface V2Option { code: string; label: string }
export interface V2Question {
  question_id: string; stage: "explore" | "confirm"; text: string; options: V2Option[];
  max_select?: number; exclusive_codes?: string[];
}
export interface V2Route {
  done: boolean; next: V2Question | null; answered: number; path_length: number; max_total: number;
  path: string[]; dropped: string[]; stage: string;
}
export type V2Answers = Record<string, string | string[]>;
export interface V2Filters { korean_only: boolean; os: "windows" | "mac" | "linux"; min_positive: number; min_reviews: number }
export interface V2Tag { tagid: number; name: string; family: string }
export interface V2Card {
  type_id: string; name: string; tagline: string; is_discovering: boolean; main_tags: V2Tag[]; extra_tags: V2Tag[];
  positive_tags: V2Tag[]; evidence: (V2Tag & { answers: { question_id: string; text: string; answer: string }[] })[];
  empty_text: string | null; question_count: number;
}
export interface V2Game {
  appid: number; name: string; image: string | null; store_url: string; short_description: string | null;
  description_is_korean: boolean; korean: "true" | "false" | "unknown"; tags: string[];
  kind: "personalized" | "reference"; reason: string; library_note: boolean;
  review: { label: string | null; positive_pct: number | null; total_reviews: number; source: string; free_game_note?: boolean };
}
export interface V2Counts {
  catalog_total: number; content_eligible: number; passing_filters: number; owned_excluded: number;
  strong_avoid_excluded: number; candidates: number; recommended: number; personalized: number; reference: number;
}
export interface V2Recs {
  status: "ok" | "catalog_unavailable"; items?: V2Game[]; counts?: V2Counts; snapshot_id?: string;
  library?: { linked: boolean; used: boolean; reason: string | null }; filters?: V2Filters; repro_key?: string;
}
export interface V2Library {
  status: "none" | "loading" | "ok" | "empty" | "private_or_unavailable" | "error";
  error_code?: string | null; fetched_at?: string | null; expires_at?: string; use_for_reco?: boolean;
  exclude_owned?: boolean; meta_status?: string | null; owned_count?: number;
  profile?: { enabled: boolean; reason: string | null; analyzed_count: number; tagged_count: number; tagged_weight_share: number };
  games?: { appid: number; name: string | null; minutes: number | null; analysis_excluded: boolean; analyzed: boolean; tagged: boolean | null }[];
}

export const V2KEYS = { session: "gembti:v2:session", answers: "gembti:v2:answers", history: "gembti:v2:history",
  result: "gembti:v2:result", extra: "gembti:v2:extra" };
export const DEFAULT_FILTERS: V2Filters = { korean_only: false, os: "windows", min_positive: 75, min_reviews: 500 };

export async function ensureSession(): Promise<V2Session> {
  const s = load<V2Session>("local", V2KEYS.session);
  if (s) return s;
  const n = await api<V2Session>("/v2/sessions", {});
  save("local", V2KEYS.session, { session_key: n.session_key, owner_key: n.owner_key });
  return n;
}

export const LIB_ERRORS: Record<string, string> = {
  invalid_input: "17자리 SteamID64 또는 steamcommunity.com/profiles/… · /id/… 주소만 지원해요. 닉네임은 쓸 수 없어요.",
  vanity_not_found: "그 주소의 프로필을 찾지 못했어요.",
  no_api_key: "지금은 서버에 Steam 연결 설정이 없어 라이브러리를 불러올 수 없어요. 질문만으로 계속할 수 있어요.",
  api_key_rejected: "Steam 연결 설정에 문제가 있어 불러오지 못했어요. 질문만으로 계속할 수 있어요.",
  rate_limited: "Steam 요청이 잠시 많아요. 조금 뒤 다시 시도해주세요.",
  timeout: "Steam 응답이 늦어 불러오지 못했어요. 다시 시도해주세요.",
  upstream_error: "Steam에서 정보를 받지 못했어요. 다시 시도해주세요.",
};
