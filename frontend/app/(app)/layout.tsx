import { redirect } from "next/navigation";
import { AuthProvider } from "@/components/AuthContext";
import { Nav } from "@/components/Nav";
import { getMe } from "@/lib/auth.server";

export const dynamic = "force-dynamic";

/** 受保護頁面的殼層：未登入一律導向 /login（尚未建立帳號的相容模式除外）。 */
export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const me = await getMe();
  if (me.auth_enabled && !me.authenticated) {
    redirect("/login");
  }
  return (
    <AuthProvider me={me}>
      <div className="relative z-10 flex min-h-full flex-col">
        <Nav />
        <main className="mx-auto w-full max-w-6xl flex-1 px-4 py-8 sm:px-6 sm:py-10">
          {children}
        </main>
        <footer className="border-t border-line-soft px-4 py-5 text-center text-[11px] tracking-widest text-ink-faint uppercase sm:px-6">
          僅供分析與研究 · 非投資建議
        </footer>
      </div>
    </AuthProvider>
  );
}
