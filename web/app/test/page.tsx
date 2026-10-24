"use client";
import { useRouter } from "next/navigation";
import Survey, { type SurveyData } from "@/components/Survey";
import TopBar from "@/components/TopBar";
import { api } from "@/lib/api";
import { KEYS, save } from "@/lib/storage";
import type { ResultPayload } from "@/lib/types";

export default function TestPage() {
  const router = useRouter();
  const submit = async (d: SurveyData) => {
    const r = await api<ResultPayload>("/submissions", {
      answers: d.answers,
      free_texts: Object.fromEntries(Object.entries(d.free_texts).filter(([, v]) => v.trim())),
      experiment_consent: d.experiment_consent,
      filters: d.filters,
      played: d.played.map((p) => p.appid),
    });
    save("session", KEYS.result, r);
    save("local", KEYS.last, { submission_id: r.submission_id, owner_key: r.owner_key });
    router.push("/result/");
  };
  return (
    <main className="shell">
      <TopBar />
      <Survey storageKey={KEYS.progress} onSubmit={submit} />
    </main>
  );
}
