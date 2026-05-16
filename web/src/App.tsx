import { FormEvent, useEffect, useMemo, useState } from "react"
import {
  Activity,
  Loader2,
  LogOut,
  Moon,
  Shield,
  Sun,
  Wrench,
} from "lucide-react"
import type { LucideIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; username: string }

type PageId = "monitor" | "tools" | "openvpn"
type Theme = "light" | "dark"

const pages: Array<{
  id: PageId
  label: string
  icon: LucideIcon
}> = [
  { id: "monitor", label: "监控", icon: Activity },
  { id: "tools", label: "工具", icon: Wrench },
  { id: "openvpn", label: "OpenVPN", icon: Shield },
]

function App() {
  const [auth, setAuth] = useState<AuthState>({ status: "loading" })
  const [activePage, setActivePage] = useState<PageId>("monitor")
  const [theme, setTheme] = useTheme()

  useEffect(() => {
    let cancelled = false
    async function loadSession() {
      try {
        const response = await fetch("/api/auth/me", { credentials: "same-origin" })
        const data = (await response.json()) as { authenticated: boolean; username?: string }
        if (cancelled) {
          return
        }
        if (data.authenticated && data.username) {
          setAuth({ status: "authenticated", username: data.username })
        } else {
          setAuth({ status: "anonymous" })
        }
      } catch {
        if (!cancelled) {
          setAuth({ status: "anonymous" })
        }
      }
    }
    loadSession()
    return () => {
      cancelled = true
    }
  }, [])

  async function handleLogout() {
    await fetch("/api/auth/logout", {
      method: "POST",
      credentials: "same-origin",
    })
    setAuth({ status: "anonymous" })
  }

  if (auth.status === "loading") {
    return (
      <main className="grid min-h-svh place-items-center bg-background text-foreground">
        <Loader2 className="size-6 animate-spin text-muted-foreground" aria-label="Loading" />
      </main>
    )
  }

  if (auth.status === "anonymous") {
    return <LoginScreen onLoggedIn={(username) => setAuth({ status: "authenticated", username })} />
  }

  const active = pages.find((page) => page.id === activePage) ?? pages[0]
  const ActiveIcon = active.icon

  return (
    <main className="min-h-svh bg-background text-foreground">
      <header className="sticky top-0 z-10 border-b bg-background/95 backdrop-blur">
        <div className="mx-auto flex w-full max-w-6xl flex-col gap-3 px-4 py-3 md:flex-row md:items-center md:justify-between md:px-6">
          <div className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <div className="grid size-9 place-items-center rounded-md bg-foreground text-background">
                <Shield className="size-4" />
              </div>
              <div>
                <div className="text-base font-semibold">DROS</div>
                <div className="text-xs text-muted-foreground">{auth.username}</div>
              </div>
            </div>
            <div className="flex items-center gap-1 md:hidden">
              <ThemeButton theme={theme} onToggle={() => setTheme(toggleTheme(theme))} />
              <Button variant="ghost" size="icon" onClick={handleLogout} aria-label="退出登录">
                <LogOut className="size-4" />
              </Button>
            </div>
          </div>

          <nav className="flex gap-1 overflow-x-auto" aria-label="主导航">
            {pages.map((page) => {
              const Icon = page.icon
              const selected = page.id === activePage
              return (
                <button
                  key={page.id}
                  className={cn(
                    "inline-flex h-10 min-w-fit items-center gap-2 rounded-md px-3 text-sm font-medium transition-colors",
                    selected
                      ? "bg-foreground text-background"
                      : "text-muted-foreground hover:bg-muted hover:text-foreground",
                  )}
                  type="button"
                  onClick={() => setActivePage(page.id)}
                  aria-current={selected ? "page" : undefined}
                >
                  <Icon className="size-4" />
                  {page.label}
                </button>
              )
            })}
          </nav>

          <div className="hidden items-center gap-2 md:flex">
            <ThemeButton theme={theme} onToggle={() => setTheme(toggleTheme(theme))} />
            <Button variant="outline" onClick={handleLogout}>
              <LogOut className="size-4" />
              退出
            </Button>
          </div>
        </div>
      </header>

      <section className="mx-auto flex w-full max-w-6xl flex-1 flex-col px-4 py-5 md:px-6 md:py-7">
        <div className="mb-4 flex items-center gap-3">
          <ActiveIcon className="size-5 text-muted-foreground" />
          <h1 className="text-xl font-semibold tracking-normal md:text-2xl">{active.label}</h1>
        </div>
        <div className="min-h-[calc(100svh-11rem)] rounded-md border border-dashed border-border bg-muted/25" />
      </section>
    </main>
  )
}

function LoginScreen({ onLoggedIn }: { onLoggedIn: (username: string) => void }) {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [remember, setRemember] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState("")

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setSubmitting(true)
    setError("")
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password, remember }),
      })
      if (!response.ok) {
        setError("用户名或密码不正确")
        return
      }
      const data = (await response.json()) as { username: string }
      onLoggedIn(data.username)
    } catch {
      setError("登录请求失败")
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="grid min-h-svh bg-background text-foreground md:grid-cols-[1fr_28rem]">
      <section className="hidden border-r bg-muted/35 px-8 py-10 md:flex md:flex-col md:justify-between">
        <div className="flex items-center gap-3">
          <div className="grid size-10 place-items-center rounded-md bg-foreground text-background">
            <Shield className="size-5" />
          </div>
          <div className="text-lg font-semibold">DROS</div>
        </div>
        <div className="max-w-lg">
          <div className="mb-4 text-5xl font-semibold tracking-normal">Gateway Console</div>
          <div className="h-1 w-20 rounded-full bg-primary" />
        </div>
      </section>

      <section className="flex min-h-svh items-center px-5 py-8">
        <div className="mx-auto w-full max-w-sm">
          <div className="mb-8 flex items-center gap-3 md:hidden">
            <div className="grid size-10 place-items-center rounded-md bg-foreground text-background">
              <Shield className="size-5" />
            </div>
            <div className="text-lg font-semibold">DROS</div>
          </div>

          <form className="space-y-5" onSubmit={handleSubmit}>
            <div>
              <h1 className="text-2xl font-semibold tracking-normal">登录</h1>
            </div>

            <label className="block space-y-2">
              <span className="text-sm font-medium">用户名</span>
              <input
                className="h-10 w-full rounded-md border bg-background px-3 text-sm outline-none transition-colors focus:border-ring focus:ring-2 focus:ring-ring/20"
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                autoComplete="username"
                required
              />
            </label>

            <label className="block space-y-2">
              <span className="text-sm font-medium">密码</span>
              <input
                className="h-10 w-full rounded-md border bg-background px-3 text-sm outline-none transition-colors focus:border-ring focus:ring-2 focus:ring-ring/20"
                type="password"
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                autoComplete="current-password"
                required
              />
            </label>

            <div className="grid grid-cols-2 rounded-md border bg-muted p-1">
              <button
                className={cn(
                  "h-9 rounded-sm text-sm font-medium transition-colors",
                  !remember && "bg-background shadow-sm",
                )}
                type="button"
                onClick={() => setRemember(false)}
              >
                临时登录
              </button>
              <button
                className={cn(
                  "h-9 rounded-sm text-sm font-medium transition-colors",
                  remember && "bg-background shadow-sm",
                )}
                type="button"
                onClick={() => setRemember(true)}
              >
                长期登录
              </button>
            </div>

            {error ? <div className="text-sm text-destructive">{error}</div> : null}

            <Button className="w-full" type="submit" disabled={submitting}>
              {submitting ? <Loader2 className="size-4 animate-spin" /> : null}
              登录
            </Button>
          </form>
        </div>
      </section>
    </main>
  )
}

function ThemeButton({ theme, onToggle }: { theme: Theme; onToggle: () => void }) {
  return (
    <Button variant="ghost" size="icon" onClick={onToggle} aria-label="切换亮色暗色">
      {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
    </Button>
  )
}

function useTheme(): [Theme, (theme: Theme) => void] {
  const initialTheme = useMemo<Theme>(() => {
    const saved = window.localStorage.getItem("dros-theme")
    if (saved === "light" || saved === "dark") {
      return saved
    }
    return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"
  }, [])
  const [theme, setThemeState] = useState<Theme>(initialTheme)

  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark")
    window.localStorage.setItem("dros-theme", theme)
  }, [theme])

  return [theme, setThemeState]
}

function toggleTheme(theme: Theme): Theme {
  return theme === "dark" ? "light" : "dark"
}

export default App
