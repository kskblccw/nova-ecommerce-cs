"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  AlertCircle,
  ArrowRight,
  Bot,
  Eye,
  EyeOff,
  Loader2,
  Lock,
  Phone,
  ShieldCheck,
  Sparkles,
  UserRound,
  Zap,
} from "lucide-react";

import { BrandMark } from "@/components/brand";
import { ThemeToggle } from "@/components/theme-toggle";
import { api } from "@/lib/api";

const FEATURES = [
  { icon: Bot, title: "多智能体协作", desc: "订单、商品、售后、FAQ 专业分工，自动路由" },
  { icon: Zap, title: "确定性 + 秒级响应", desc: "规则预路由命中即执行，一次分类兜底" },
  { icon: ShieldCheck, title: "安全可追溯", desc: "变更二次确认、手机号脱敏、全量审计" },
];

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [phone, setPhone] = useState("");
  const [password, setPassword] = useState("");
  const [name, setName] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .me()
      .then(() => router.replace("/chat"))
      .catch(() => undefined);
  }, [router]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    if (!/^1[3-9]\d{9}$/.test(phone)) {
      setError("请输入有效的 11 位手机号");
      return;
    }
    if (password.length < 6) {
      setError("密码至少 6 位");
      return;
    }

    setLoading(true);
    try {
      if (mode === "login") {
        await api.login(phone, password);
      } else {
        await api.register(phone, password, name);
      }
      router.replace("/chat");
    } catch (err) {
      setError(err instanceof Error ? err.message : "操作失败，请稍后重试");
    } finally {
      setLoading(false);
    }
  }

  function useDemo() {
    setMode("login");
    setPhone("13800001001");
    setPassword("shop123456");
    setError(null);
  }

  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.05fr_1fr]">
      <section
        className="relative hidden overflow-hidden p-10 lg:flex lg:flex-col lg:justify-between"
        style={{
          background:
            "radial-gradient(1200px 600px at 15% 10%, rgba(14,159,110,0.28), transparent 60%)," +
            "linear-gradient(150deg, #05271c 0%, #063a29 45%, #04150f 100%)",
        }}
      >
        <div className="pointer-events-none absolute inset-0 grid-pattern opacity-30" />
        <div className="pointer-events-none absolute -right-24 top-24 h-72 w-72 rounded-full blur-3xl"
          style={{ background: "rgba(52, 211, 153, 0.35)" }} />
        <div className="pointer-events-none absolute -bottom-24 left-10 h-64 w-64 rounded-full blur-3xl"
          style={{ background: "rgba(16, 185, 129, 0.22)" }} />

        <div className="relative z-10">
          <BrandMark />
        </div>

        <div className="relative z-10 max-w-[520px]">
          <div className="chip mb-6 border-white/15 bg-white/10 text-white/90">
            <Sparkles className="h-3.5 w-3.5" />
            Enterprise Agent Workspace
          </div>
          <h1 className="text-[40px] font-semibold leading-[1.15] tracking-tight text-white">
            让每一次咨询，
            <br />
            都被认真对待。
          </h1>
          <p className="mt-4 max-w-[440px] text-[15px] leading-relaxed text-white/70">
            Nova 把订单、物流、售后与商品知识整合进同一套智能体工作流，
            让客服响应更快，让操作更可信。
          </p>

          <div className="mt-10 space-y-4">
            {FEATURES.map((feature) => (
              <div key={feature.title} className="flex items-start gap-3">
                <div className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-white/15 bg-white/10">
                  <feature.icon className="h-4 w-4 text-emerald-200" />
                </div>
                <div>
                  <div className="text-[14px] font-medium text-white">{feature.title}</div>
                  <div className="mt-0.5 text-[12.5px] text-white/60">{feature.desc}</div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="relative z-10 flex items-center gap-6 text-[12px] text-white/55">
          <span>5 个智能体</span>
          <span className="h-1 w-1 rounded-full bg-white/30" />
          <span>秒级响应</span>
          <span className="h-1 w-1 rounded-full bg-white/30" />
          <span>全链路可追溯</span>
        </div>
      </section>

      <section className="relative flex items-center justify-center px-5 py-10 sm:px-8">
        <div className="absolute right-5 top-5 flex items-center gap-2">
          <ThemeToggle />
        </div>

        <div className="w-full max-w-[400px]">
          <div className="mb-8 flex justify-center lg:hidden">
            <BrandMark />
          </div>

          <div className="card p-6 sm:p-8">
            <div className="mb-6">
              <h2 className="text-[22px] font-semibold tracking-tight">
                {mode === "login" ? "欢迎回来" : "创建账号"}
              </h2>
              <p className="mt-1.5 text-[13px]" style={{ color: "var(--muted)" }}>
                {mode === "login"
                  ? "登录后即可查询订单、物流与售后进度。"
                  : "使用手机号注册，几秒钟开始体验。"}
              </p>
            </div>

            <div className="mb-6 grid grid-cols-2 gap-1 rounded-xl p-1" style={{ background: "var(--surface-soft)" }}>
              {(["login", "register"] as const).map((item) => (
                <button
                  key={item}
                  type="button"
                  onClick={() => {
                    setMode(item);
                    setError(null);
                  }}
                  className="rounded-lg py-2 text-[13px] font-medium transition"
                  style={{
                    background: mode === item ? "var(--surface)" : "transparent",
                    color: mode === item ? "var(--text)" : "var(--muted)",
                    boxShadow: mode === item ? "var(--shadow-sm)" : "none",
                  }}
                >
                  {item === "login" ? "登录" : "注册"}
                </button>
              ))}
            </div>

            <form onSubmit={submit} className="space-y-3.5">
              {mode === "register" && (
                <label className="block">
                  <span className="mb-1.5 block text-[12px] font-medium" style={{ color: "var(--muted)" }}>
                    昵称
                  </span>
                  <div className="relative">
                    <UserRound className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2" style={{ color: "var(--faint)" }} />
                    <input
                      className="input pl-10"
                      value={name}
                      onChange={(event) => setName(event.target.value)}
                      placeholder="怎么称呼您（可选）"
                      maxLength={50}
                    />
                  </div>
                </label>
              )}

              <label className="block">
                <span className="mb-1.5 block text-[12px] font-medium" style={{ color: "var(--muted)" }}>
                  手机号
                </span>
                <div className="relative">
                  <Phone className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2" style={{ color: "var(--faint)" }} />
                  <input
                    className="input pl-10"
                    value={phone}
                    onChange={(event) => setPhone(event.target.value.replace(/[^\d]/g, ""))}
                    placeholder="请输入 11 位手机号"
                    inputMode="tel"
                    maxLength={11}
                    autoComplete="tel"
                  />
                </div>
              </label>

              <label className="block">
                <span className="mb-1.5 block text-[12px] font-medium" style={{ color: "var(--muted)" }}>
                  密码
                </span>
                <div className="relative">
                  <Lock className="pointer-events-none absolute left-3.5 top-1/2 h-4 w-4 -translate-y-1/2" style={{ color: "var(--faint)" }} />
                  <input
                    className="input pl-10 pr-11"
                    type={showPassword ? "text" : "password"}
                    value={password}
                    onChange={(event) => setPassword(event.target.value)}
                    placeholder="请输入密码（至少 6 位）"
                    autoComplete={mode === "login" ? "current-password" : "new-password"}
                  />
                  <button
                    type="button"
                    onClick={() => setShowPassword((prev) => !prev)}
                    className="absolute right-3 top-1/2 -translate-y-1/2"
                    style={{ color: "var(--faint)" }}
                    aria-label={showPassword ? "隐藏密码" : "显示密码"}
                  >
                    {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                  </button>
                </div>
              </label>

              {error && (
                <div className="flex items-start gap-2 rounded-xl px-3 py-2.5 text-[12.5px]"
                  style={{ background: "var(--danger-soft)", color: "var(--danger)" }} role="alert">
                  <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                  <span>{error}</span>
                </div>
              )}

              <button type="submit" className="btn btn-primary h-11 w-full" disabled={loading}>
                {loading ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <>
                    {mode === "login" ? "登录" : "注册并登录"}
                    <ArrowRight className="h-4 w-4" />
                  </>
                )}
              </button>
            </form>

            <div className="mt-5 flex items-center justify-between text-[12px]" style={{ color: "var(--faint)" }}>
              <span>
                {mode === "login" ? "还没有账号？" : "已有账号？"}
                <button
                  type="button"
                  className="ml-1 font-medium"
                  style={{ color: "var(--brand-strong)" }}
                  onClick={() => {
                    setMode(mode === "login" ? "register" : "login");
                    setError(null);
                  }}
                >
                  {mode === "login" ? "立即注册" : "去登录"}
                </button>
              </span>
              <button type="button" onClick={useDemo} className="font-medium" style={{ color: "var(--brand-strong)" }}>
                使用演示账号
              </button>
            </div>
          </div>

          <p className="mt-5 text-center text-[11.5px] leading-relaxed" style={{ color: "var(--faint)" }}>
            演示账号 13800001001 / shop123456 · 管理员 13900000000
            <br />
            登录即表示您同意本系统的演示使用条款。
          </p>
        </div>
      </section>
    </div>
  );
}
