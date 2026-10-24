export type Code = "A" | "B" | "C" | "D" | "E" | "X";
export interface Option { code: Code; label: string }
export interface Question { question_id: string; text: string; kind: "general" | "special"; options: Option[] }
export interface FreeText { free_text_id: string; text: string; max_chars: number }
export interface Questionnaire { intro: string; questions: Question[]; free_texts: FreeText[]; versions: Record<string, string> }
export interface TagRef { tagid: number; name: string }
export interface Evidence extends TagRef { answers: { question_id: string; axis: string; answer: string }[] }
export interface Card {
  type_id: string; name: string; tagline: string; is_discovering: boolean;
  main_tags: TagRef[]; extra_social_tags: TagRef[]; positive_tags: TagRef[]; evidence: Evidence[];
  empty_text: string | null;
  answers: { question_id: string; code: Code; axis: string; answer: string }[];
}
export interface Game {
  appid: number; name: string; image: string | null; store_url: string; short_description: string | null;
  description_is_korean: boolean; korean: "true" | "false" | "unknown"; tags: string[];
  kind?: "personalized" | "reference"; matched_tags?: string[]; reason?: string;
}
export interface Recs {
  status: "ok" | "catalog_unavailable"; message?: string; items?: Game[];
  personalized_count?: number; reference_count?: number; shortfall?: number;
}
export interface Filters { korean_only: boolean; os: "windows" | "mac" | "linux" }
export interface ResultPayload {
  submission_id: string; owner_key?: string; card: Card; recommendations: Recs; filters: Filters; played: number[];
}
