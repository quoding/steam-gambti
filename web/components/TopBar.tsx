import Link from "next/link";

export default function TopBar({ right }: { right?: React.ReactNode }) {
  return (
    <header className="topbar">
      <Link href="/" className="brand">겜<b>BTI</b></Link>
      <div>{right}</div>
    </header>
  );
}
