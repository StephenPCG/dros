import { useEffect, useMemo, useState } from "react"
import type { FormEvent, ReactNode } from "react"
import {
  Activity,
  Ban,
  ChevronLeft,
  Download,
  KeyRound,
  Loader2,
  LogOut,
  Menu,
  Moon,
  Plus,
  RefreshCw,
  RotateCw,
  Server,
  Shield,
  Sun,
  X,
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
type OpenVPNProfileKind = "server" | "client"

type OpenVPNInstance = {
  name: string
  root: string
  serverProfiles: number
  clientProfiles: number
  serverCerts: number
  clientCerts: number
  crlExists: boolean
}

type OpenVPNProfile = {
  kind: OpenVPNProfileKind
  name: string
  path: string
  latestCert: string | null
  outputFiles: string[]
}

type OpenVPNCert = {
  certId: string
  path: string
  latest: boolean
  revoked: boolean
}

type OpenVPNCreateForm = {
  kind: OpenVPNProfileKind
  name: string
  endpoint: string
  cn: string
  network: string
  netmask: string
}

type MonitorSummary = {
  system: {
    hostname: string
    kernel: string
    cpuPercent: number | null
    loadavg: number[] | null
    uptimeSeconds: number | null
  }
  memory: {
    totalBytes: number | null
    availableBytes: number | null
    usedBytes: number | null
    usedPercent: number | null
  }
  interfaces: Array<{
    name: string
    operstate: string
    rxBytes: number
    txBytes: number
    rxBytesPerSecond: number
    txBytesPerSecond: number
  }>
}

const pages: Array<{
  id: PageId
  label: string
  icon: LucideIcon
}> = [
  { id: "monitor", label: "监控", icon: Activity },
  { id: "tools", label: "工具", icon: Wrench },
  { id: "openvpn", label: "OpenVPN", icon: Shield },
]

const pagePaths: Record<PageId, string> = {
  monitor: "/monitor",
  tools: "/tools",
  openvpn: "/openvpn",
}

function App() {
  const [auth, setAuth] = useState<AuthState>({ status: "loading" })
  const [activePage, setActivePage] = useState<PageId>(() => pageFromPath(window.location.pathname))
  const [navOpen, setNavOpen] = useState(false)
  const [theme, setTheme] = useTheme()

  useEffect(() => {
    function handlePopState() {
      setActivePage(pageFromPath(window.location.pathname))
    }
    window.addEventListener("popstate", handlePopState)
    return () => window.removeEventListener("popstate", handlePopState)
  }, [])

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

  useEffect(() => {
    if (!navOpen) {
      return
    }
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setNavOpen(false)
      }
    }
    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [navOpen])

  async function handleLogout() {
    await fetch("/api/auth/logout", {
      method: "POST",
      credentials: "same-origin",
    })
    setAuth({ status: "anonymous" })
  }

  function handlePageChange(page: PageId) {
    setActivePage(page)
    setNavOpen(false)
    const path = pagePaths[page]
    if (window.location.pathname !== path) {
      window.history.pushState({ page }, "", path)
    }
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
        <div className="mx-auto flex w-full max-w-6xl items-center justify-between gap-3 px-4 py-3 md:px-6">
          <div className="flex min-w-0 items-center gap-2 md:gap-3">
            <div className="flex min-w-0 items-center gap-3">
              <div className="grid size-9 place-items-center rounded-md bg-foreground text-background">
                <Shield className="size-4" />
              </div>
              <div className="min-w-0">
                <div className="text-base font-semibold">DROS</div>
                <div className="text-xs text-muted-foreground">{auth.username}</div>
              </div>
            </div>
            <Button
              className="md:hidden"
              variant="ghost"
              size="icon"
              onClick={() => setNavOpen((current) => !current)}
              aria-label={navOpen ? "关闭导航" : "打开导航"}
              aria-controls="mobile-nav-drawer"
              aria-expanded={navOpen}
              title={navOpen ? "关闭导航" : "打开导航"}
            >
              {navOpen ? <X className="size-4" /> : <Menu className="size-4" />}
            </Button>
          </div>

          <nav className="hidden gap-1 overflow-x-auto md:flex" aria-label="主导航">
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
                  onClick={() => handlePageChange(page.id)}
                  aria-current={selected ? "page" : undefined}
                >
                  <Icon className="size-4" />
                  {page.label}
                </button>
              )
            })}
          </nav>

          <div className="flex items-center gap-1 md:gap-2">
            <ThemeButton theme={theme} onToggle={() => setTheme(toggleTheme(theme))} />
            <Button className="md:hidden" variant="ghost" size="icon" onClick={handleLogout} aria-label="退出登录">
              <LogOut className="size-4" />
            </Button>
            <Button className="hidden md:inline-flex" variant="outline" onClick={handleLogout}>
              <LogOut className="size-4" />
              退出
            </Button>
          </div>
        </div>
      </header>

      {navOpen ? <MobileNavigationDrawer activePage={activePage} onSelect={handlePageChange} onClose={() => setNavOpen(false)} /> : null}

      <section className="mx-auto flex w-full max-w-6xl flex-1 flex-col px-4 py-5 md:px-6 md:py-7">
        <div className="mb-4 flex items-center gap-3">
          <ActiveIcon className="size-5 text-muted-foreground" />
          <h1 className="text-xl font-semibold tracking-normal md:text-2xl">{active.label}</h1>
        </div>
        {activePage === "monitor" ? (
          <MonitorPage />
        ) : activePage === "openvpn" ? (
          <OpenVPNPage />
        ) : (
          <div className="min-h-[calc(100svh-11rem)] rounded-md border border-dashed border-border bg-muted/25" />
        )}
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

function MobileNavigationDrawer({
  activePage,
  onSelect,
  onClose,
}: {
  activePage: PageId
  onSelect: (page: PageId) => void
  onClose: () => void
}) {
  return (
    <div className="fixed inset-0 z-20 md:hidden">
      <button
        className="absolute inset-0 bg-background/70 backdrop-blur-sm"
        type="button"
        onClick={onClose}
        aria-label="关闭导航"
      />
      <aside
        id="mobile-nav-drawer"
        className="absolute left-0 top-0 flex h-full w-72 max-w-[85vw] flex-col border-r bg-background shadow-lg"
        aria-label="移动端导航"
      >
        <div className="flex items-center justify-between gap-3 border-b px-4 py-3">
          <div className="flex items-center gap-3">
            <div className="grid size-9 place-items-center rounded-md bg-foreground text-background">
              <Shield className="size-4" />
            </div>
            <div className="text-base font-semibold">DROS</div>
          </div>
          <Button variant="ghost" size="icon" onClick={onClose} aria-label="关闭导航" title="关闭导航">
            <X className="size-4" />
          </Button>
        </div>
        <nav className="flex flex-col gap-1 p-3" aria-label="主导航">
          {pages.map((page) => {
            const Icon = page.icon
            const selected = page.id === activePage
            return (
              <button
                key={page.id}
                className={cn(
                  "flex h-11 w-full items-center gap-3 rounded-md px-3 text-left text-sm font-medium transition-colors",
                  selected
                    ? "bg-foreground text-background"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground",
                )}
                type="button"
                onClick={() => onSelect(page.id)}
                aria-current={selected ? "page" : undefined}
              >
                <Icon className="size-4" />
                {page.label}
              </button>
            )
          })}
        </nav>
      </aside>
    </div>
  )
}

function MonitorPage() {
  const [summary, setSummary] = useState<MonitorSummary | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  useEffect(() => {
    loadSummary()
    const timer = window.setInterval(loadSummary, 5000)
    return () => window.clearInterval(timer)
  }, [])

  async function loadSummary() {
    setError("")
    try {
      const data = await apiJson<MonitorSummary>("/api/monitor/summary")
      setSummary(data)
    } catch (err) {
      setError(errorMessage(err, "加载监控数据失败"))
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-[calc(100svh-11rem)] space-y-5">
      <div className="flex items-center justify-between gap-3">
        <div className="text-sm text-muted-foreground">
          {summary ? `${summary.system.hostname} · ${summary.system.kernel}` : "monitor"}
        </div>
        <Button variant="outline" onClick={loadSummary} disabled={loading}>
          {loading ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          刷新
        </Button>
      </div>

      {error ? <div className="rounded-md border border-destructive/35 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</div> : null}

      <div className="grid gap-3 md:grid-cols-3">
        <MetricPanel
          label="CPU"
          value={summary?.system.cpuPercent == null ? "-" : `${summary.system.cpuPercent}%`}
          detail={summary?.system.loadavg ? `load ${summary.system.loadavg.join(" / ")}` : "load -"}
        />
        <MetricPanel
          label="内存"
          value={summary?.memory.usedPercent == null ? "-" : `${summary.memory.usedPercent}%`}
          detail={`${formatBytes(summary?.memory.usedBytes)} / ${formatBytes(summary?.memory.totalBytes)}`}
        />
        <MetricPanel
          label="运行时间"
          value={formatDuration(summary?.system.uptimeSeconds)}
          detail="system uptime"
        />
      </div>

      <div className="overflow-hidden rounded-md border bg-background">
        <div className="border-b px-3 py-2 text-sm font-medium">接口速度</div>
        {summary && summary.interfaces.length > 0 ? (
          <div className="max-w-full overflow-x-auto">
            <table className="w-full min-w-[42rem] text-left text-sm">
              <thead className="border-b bg-muted/50 text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="px-3 py-2 font-medium">接口</th>
                  <th className="px-3 py-2 font-medium">状态</th>
                  <th className="px-3 py-2 text-right font-medium">RX</th>
                  <th className="px-3 py-2 text-right font-medium">TX</th>
                  <th className="px-3 py-2 text-right font-medium">RX/s</th>
                  <th className="px-3 py-2 text-right font-medium">TX/s</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {summary.interfaces.map((iface) => (
                  <tr key={iface.name}>
                    <td className="px-3 py-3 font-mono text-xs">{iface.name}</td>
                    <td className="px-3 py-3">{iface.operstate}</td>
                    <td className="px-3 py-3 text-right font-mono text-xs">{formatBytes(iface.rxBytes)}</td>
                    <td className="px-3 py-3 text-right font-mono text-xs">{formatBytes(iface.txBytes)}</td>
                    <td className="px-3 py-3 text-right font-mono text-xs">{formatBytes(iface.rxBytesPerSecond)}</td>
                    <td className="px-3 py-3 text-right font-mono text-xs">{formatBytes(iface.txBytesPerSecond)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="px-3 py-10 text-sm text-muted-foreground">
            {loading ? "正在加载" : "暂无接口数据"}
          </div>
        )}
      </div>
    </div>
  )
}

function MetricPanel({ label, value, detail }: { label: string; value: ReactNode; detail: ReactNode }) {
  return (
    <div className="rounded-md border bg-background px-3 py-3">
      <div className="text-xs font-medium uppercase text-muted-foreground">{label}</div>
      <div className="mt-2 text-2xl font-semibold tracking-normal">{value}</div>
      <div className="mt-1 text-xs text-muted-foreground">{detail}</div>
    </div>
  )
}

function OpenVPNPage() {
  const [instances, setInstances] = useState<OpenVPNInstance[]>([])
  const [selectedInstance, setSelectedInstance] = useState("")
  const [profiles, setProfiles] = useState<OpenVPNProfile[]>([])
  const [loadingInstances, setLoadingInstances] = useState(true)
  const [loadingProfiles, setLoadingProfiles] = useState(false)
  const [error, setError] = useState("")
  const [action, setAction] = useState("")
  const [createOpen, setCreateOpen] = useState(false)
  const [createForm, setCreateForm] = useState<OpenVPNCreateForm>(emptyOpenVPNCreateForm())
  const [certProfile, setCertProfile] = useState<OpenVPNProfile | null>(null)
  const [certs, setCerts] = useState<OpenVPNCert[]>([])
  const [loadingCerts, setLoadingCerts] = useState(false)

  useEffect(() => {
    loadInstances()
  }, [])

  useEffect(() => {
    if (!selectedInstance) {
      setProfiles([])
      return
    }
    loadProfiles(selectedInstance)
  }, [selectedInstance])

  async function loadInstances() {
    setLoadingInstances(true)
    setError("")
    try {
      const data = await apiJson<{ instances: OpenVPNInstance[] }>("/api/openvpn/instances")
      setInstances(data.instances)
      setSelectedInstance((current) => {
        if (current && data.instances.some((item) => item.name === current)) {
          return current
        }
        return data.instances[0]?.name ?? ""
      })
    } catch (err) {
      setError(errorMessage(err, "加载 OpenVPN 实例失败"))
    } finally {
      setLoadingInstances(false)
    }
  }

  async function loadProfiles(instance: string) {
    setLoadingProfiles(true)
    setError("")
    try {
      const data = await apiJson<{ profiles: OpenVPNProfile[] }>(
        `/api/openvpn/instances/${encodeURIComponent(instance)}/profiles`,
      )
      setProfiles(data.profiles)
    } catch (err) {
      setProfiles([])
      setError(errorMessage(err, "加载 OpenVPN Profile 失败"))
    } finally {
      setLoadingProfiles(false)
    }
  }

  async function refreshAll() {
    await loadInstances()
    if (selectedInstance) {
      await loadProfiles(selectedInstance)
    }
  }

  async function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!selectedInstance) {
      return
    }
    setAction("create")
    setError("")
    try {
      const payload: Record<string, string> = {
        kind: createForm.kind,
        name: createForm.name.trim(),
      }
      for (const key of ["endpoint", "cn", "network", "netmask"] as const) {
        const value = createForm[key].trim()
        if (value) {
          payload[key] = value
        }
      }
      await apiJson(`/api/openvpn/instances/${encodeURIComponent(selectedInstance)}/profiles`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      })
      setCreateOpen(false)
      setCreateForm(emptyOpenVPNCreateForm())
      await refreshAll()
    } catch (err) {
      setError(errorMessage(err, "创建 Profile 失败"))
    } finally {
      setAction("")
    }
  }

  async function renewProfile(profile: OpenVPNProfile) {
    if (!selectedInstance) {
      return
    }
    setAction(`renew:${profile.kind}:${profile.name}`)
    setError("")
    try {
      await apiJson(
        `/api/openvpn/instances/${encodeURIComponent(selectedInstance)}/profiles/${profile.kind}/${encodeURIComponent(profile.name)}/renew`,
        { method: "POST" },
      )
      await refreshAll()
    } catch (err) {
      setError(errorMessage(err, "Renew 失败"))
    } finally {
      setAction("")
    }
  }

  async function openCerts(profile: OpenVPNProfile) {
    if (!selectedInstance) {
      return
    }
    setCertProfile(profile)
    setLoadingCerts(true)
    setError("")
    try {
      const data = await apiJson<{ certs: OpenVPNCert[] }>(
        `/api/openvpn/instances/${encodeURIComponent(selectedInstance)}/profiles/${profile.kind}/${encodeURIComponent(profile.name)}/certs`,
      )
      setCerts(data.certs)
    } catch (err) {
      setCerts([])
      setError(errorMessage(err, "加载证书失败"))
    } finally {
      setLoadingCerts(false)
    }
  }

  async function revokeCert(certId: string) {
    if (!selectedInstance || !certProfile) {
      return
    }
    setAction(`revoke:${certId}`)
    setError("")
    try {
      await apiJson(
        `/api/openvpn/instances/${encodeURIComponent(selectedInstance)}/profiles/${certProfile.kind}/${encodeURIComponent(certProfile.name)}/certs/${encodeURIComponent(certId)}/revoke`,
        { method: "POST" },
      )
      await openCerts(certProfile)
      await refreshAll()
    } catch (err) {
      setError(errorMessage(err, "吊销证书失败"))
    } finally {
      setAction("")
    }
  }

  function downloadProfile(profile: OpenVPNProfile) {
    if (!selectedInstance) {
      return
    }
    window.location.assign(
      `/api/openvpn/instances/${encodeURIComponent(selectedInstance)}/profiles/${profile.kind}/${encodeURIComponent(profile.name)}/download`,
    )
  }

  const selected = instances.find((item) => item.name === selectedInstance) ?? null

  return (
    <div className="min-h-[calc(100svh-11rem)] space-y-5">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={refreshAll} disabled={loadingInstances || loadingProfiles}>
            {loadingInstances || loadingProfiles ? (
              <Loader2 className="size-4 animate-spin" />
            ) : (
              <RefreshCw className="size-4" />
            )}
            刷新
          </Button>
          <Button onClick={() => setCreateOpen(true)} disabled={!selectedInstance}>
            <Plus className="size-4" />
            新建 Profile
          </Button>
        </div>
        {selected ? (
          <div className="flex flex-wrap gap-2 text-xs text-muted-foreground">
            <Metric label="server" value={selected.serverProfiles} />
            <Metric label="client" value={selected.clientProfiles} />
            <Metric label="cert" value={selected.serverCerts + selected.clientCerts} />
            <Metric label="crl" value={selected.crlExists ? "ok" : "missing"} />
          </div>
        ) : null}
      </div>

      {error ? <div className="rounded-md border border-destructive/35 bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</div> : null}

      <div className="grid min-w-0 gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
        <div className="min-w-0 rounded-md border bg-background">
          <div className="border-b px-3 py-2 text-sm font-medium">实例</div>
          {loadingInstances ? (
            <div className="flex h-24 items-center justify-center text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
            </div>
          ) : instances.length === 0 ? (
            <div className="px-3 py-8 text-sm text-muted-foreground">暂无实例</div>
          ) : (
            <div className="divide-y">
              {instances.map((item) => (
                <button
                  key={item.name}
                  className={cn(
                    "flex w-full items-center justify-between gap-3 px-3 py-3 text-left text-sm transition-colors hover:bg-muted",
                    item.name === selectedInstance && "bg-muted",
                  )}
                  type="button"
                  onClick={() => setSelectedInstance(item.name)}
                >
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{item.name}</span>
                    <span className="block truncate text-xs text-muted-foreground">{item.root}</span>
                  </span>
                  <ChevronLeft
                    className={cn(
                      "size-4 shrink-0 rotate-180 text-muted-foreground",
                      item.name === selectedInstance && "text-foreground",
                    )}
                  />
                </button>
              ))}
            </div>
          )}
        </div>

        <div className="min-w-0 overflow-hidden rounded-md border bg-background">
          <div className="flex items-center justify-between gap-3 border-b px-3 py-2">
            <div className="text-sm font-medium">Profiles</div>
            {loadingProfiles ? <Loader2 className="size-4 animate-spin text-muted-foreground" /> : null}
          </div>
          {selectedInstance && profiles.length > 0 ? (
            <div className="max-w-full overflow-x-auto">
              <table className="w-full min-w-[48rem] text-left text-sm">
                <thead className="border-b bg-muted/50 text-xs uppercase text-muted-foreground">
                  <tr>
                    <th className="sticky left-0 z-20 w-40 min-w-40 border-r bg-muted px-3 py-2 font-medium">
                      名称
                    </th>
                    <th className="px-3 py-2 font-medium">类型</th>
                    <th className="px-3 py-2 font-medium">最新证书</th>
                    <th className="px-3 py-2 font-medium">输出</th>
                    <th className="px-3 py-2 text-right font-medium">操作</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {profiles.map((profile) => (
                    <tr key={`${profile.kind}:${profile.name}`}>
                      <td className="sticky left-0 z-10 w-40 min-w-40 border-r bg-background px-3 py-3 font-medium">
                        <div className="truncate">{profile.name}</div>
                      </td>
                      <td className="px-3 py-3">
                        <KindBadge kind={profile.kind} />
                      </td>
                      <td className="px-3 py-3 font-mono text-xs">
                        {profile.latestCert ?? <span className="text-muted-foreground">none</span>}
                      </td>
                      <td className="px-3 py-3 text-xs text-muted-foreground">
                        {profile.outputFiles.length ? profile.outputFiles.map(fileName).join(", ") : "none"}
                      </td>
                      <td className="px-3 py-3">
                        <div className="flex justify-end gap-1">
                          <TooltipIconButton label="查看证书" onClick={() => openCerts(profile)}>
                            <KeyRound className="size-4" />
                          </TooltipIconButton>
                          <TooltipIconButton
                            label="renew 证书"
                            onClick={() => renewProfile(profile)}
                            disabled={action === `renew:${profile.kind}:${profile.name}`}
                          >
                            {action === `renew:${profile.kind}:${profile.name}` ? (
                              <Loader2 className="size-4 animate-spin" />
                            ) : (
                              <RotateCw className="size-4" />
                            )}
                          </TooltipIconButton>
                          <TooltipIconButton label="下载配置" onClick={() => downloadProfile(profile)}>
                            <Download className="size-4" />
                          </TooltipIconButton>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="px-3 py-10 text-sm text-muted-foreground">
              {selectedInstance ? "暂无 Profile" : "暂无实例"}
            </div>
          )}
        </div>
      </div>

      {createOpen ? (
        <Modal title="新建 Profile" onClose={() => setCreateOpen(false)}>
          <form className="space-y-4" onSubmit={handleCreate}>
            <div className="grid grid-cols-2 rounded-md border bg-muted p-1">
              {(["server", "client"] as const).map((kind) => (
                <button
                  key={kind}
                  className={cn(
                    "h-9 rounded-sm text-sm font-medium transition-colors",
                    createForm.kind === kind && "bg-background shadow-sm",
                  )}
                  type="button"
                  onClick={() => setCreateForm((current) => ({ ...current, kind }))}
                >
                  {kind === "server" ? "Server" : "Client"}
                </button>
              ))}
            </div>
            <OpenVPNInput
              label="名称"
              value={createForm.name}
              onChange={(name) => setCreateForm((current) => ({ ...current, name }))}
              required
            />
            <OpenVPNInput
              label="CN"
              value={createForm.cn}
              onChange={(cn) => setCreateForm((current) => ({ ...current, cn }))}
            />
            {createForm.kind === "server" ? (
              <div className="grid gap-3 md:grid-cols-3">
                <OpenVPNInput
                  label="Endpoint"
                  value={createForm.endpoint}
                  onChange={(endpoint) => setCreateForm((current) => ({ ...current, endpoint }))}
                />
                <OpenVPNInput
                  label="Network"
                  value={createForm.network}
                  onChange={(network) => setCreateForm((current) => ({ ...current, network }))}
                />
                <OpenVPNInput
                  label="Netmask"
                  value={createForm.netmask}
                  onChange={(netmask) => setCreateForm((current) => ({ ...current, netmask }))}
                />
              </div>
            ) : null}
            <div className="flex justify-end gap-2">
              <Button variant="outline" type="button" onClick={() => setCreateOpen(false)}>
                取消
              </Button>
              <Button type="submit" disabled={action === "create" || !createForm.name.trim()}>
                {action === "create" ? <Loader2 className="size-4 animate-spin" /> : <Plus className="size-4" />}
                创建
              </Button>
            </div>
          </form>
        </Modal>
      ) : null}

      {certProfile ? (
        <Modal title={`${certProfile.name} 证书`} onClose={() => setCertProfile(null)}>
          {loadingCerts ? (
            <div className="grid h-24 place-items-center text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
            </div>
          ) : certs.length ? (
            <div className="divide-y rounded-md border">
              {certs.map((cert) => (
                <div key={cert.certId} className="flex items-center justify-between gap-3 px-3 py-3">
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="font-mono text-xs">{cert.certId}</span>
                      {cert.latest ? <Badge>latest</Badge> : null}
                      {cert.revoked ? <Badge tone="destructive">revoked</Badge> : null}
                    </div>
                    <div className="mt-1 truncate text-xs text-muted-foreground">{cert.path}</div>
                  </div>
                  <TooltipIconButton
                    label="吊销证书"
                    onClick={() => revokeCert(cert.certId)}
                    disabled={cert.revoked || action === `revoke:${cert.certId}`}
                  >
                    {action === `revoke:${cert.certId}` ? (
                      <Loader2 className="size-4 animate-spin" />
                    ) : (
                      <Ban className="size-4" />
                    )}
                  </TooltipIconButton>
                </div>
              ))}
            </div>
          ) : (
            <div className="py-8 text-sm text-muted-foreground">暂无证书</div>
          )}
        </Modal>
      ) : null}
    </div>
  )
}

function OpenVPNInput({
  label,
  value,
  onChange,
  required,
}: {
  label: string
  value: string
  onChange: (value: string) => void
  required?: boolean
}) {
  return (
    <label className="block space-y-2">
      <span className="text-sm font-medium">{label}</span>
      <input
        className="h-10 w-full rounded-md border bg-background px-3 text-sm outline-none transition-colors focus:border-ring focus:ring-2 focus:ring-ring/20"
        value={value}
        onChange={(event) => onChange(event.target.value)}
        required={required}
      />
    </label>
  )
}

function TooltipIconButton({
  label,
  children,
  onClick,
  disabled,
  type = "button",
}: {
  label: string
  children: ReactNode
  onClick?: () => void
  disabled?: boolean
  type?: "button" | "submit" | "reset"
}) {
  return (
    <span className="group relative inline-flex">
      <Button
        variant="ghost"
        size="icon"
        type={type}
        onClick={onClick}
        disabled={disabled}
        aria-label={label}
        title={label}
      >
        {children}
      </Button>
      <span
        data-tooltip-label={label}
        className={cn(
          "pointer-events-none absolute bottom-full left-1/2 z-30 mb-2 -translate-x-1/2 whitespace-nowrap rounded-md",
          "bg-foreground px-2 py-1 text-xs font-medium text-background opacity-0 shadow-sm transition-opacity",
          "group-hover:opacity-100 group-focus-within:opacity-100",
        )}
        aria-hidden="true"
      >
        {label}
      </span>
    </span>
  )
}

function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  return (
    <div className="fixed inset-0 z-20 flex items-center justify-center bg-background/70 px-4 backdrop-blur-sm">
      <div className="w-full max-w-2xl rounded-md border bg-background shadow-lg">
        <div className="flex items-center justify-between gap-3 border-b px-4 py-3">
          <div className="text-sm font-semibold">{title}</div>
          <TooltipIconButton label="关闭" onClick={onClose}>
            <X className="size-4" />
          </TooltipIconButton>
        </div>
        <div className="max-h-[75svh] overflow-y-auto p-4">{children}</div>
      </div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string | number }) {
  return (
    <span className="inline-flex h-7 items-center gap-1 rounded-md border px-2">
      <span>{label}</span>
      <span className="font-semibold text-foreground">{value}</span>
    </span>
  )
}

function KindBadge({ kind }: { kind: OpenVPNProfileKind }) {
  return (
    <span className="inline-flex h-7 items-center gap-1 rounded-md border px-2 text-xs">
      {kind === "server" ? <Server className="size-3.5" /> : <Shield className="size-3.5" />}
      {kind}
    </span>
  )
}

function Badge({ children, tone = "default" }: { children: ReactNode; tone?: "default" | "destructive" }) {
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center rounded-md border px-2 text-xs",
        tone === "destructive"
          ? "border-destructive/35 bg-destructive/10 text-destructive"
          : "bg-muted text-muted-foreground",
      )}
    >
      {children}
    </span>
  )
}

function emptyOpenVPNCreateForm(): OpenVPNCreateForm {
  return {
    kind: "client",
    name: "",
    endpoint: "",
    cn: "",
    network: "",
    netmask: "",
  }
}

function fileName(path: string): string {
  return path.split("/").pop() || path
}

function formatBytes(value: number | null | undefined): string {
  if (value == null) {
    return "-"
  }
  const units = ["B", "KiB", "MiB", "GiB", "TiB"]
  let amount = value
  for (const unit of units) {
    if (amount < 1024 || unit === units[units.length - 1]) {
      return unit === "B" ? `${Math.round(amount)} B` : `${amount.toFixed(1)} ${unit}`
    }
    amount /= 1024
  }
  return `${value} B`
}

function formatDuration(seconds: number | null | undefined): string {
  if (seconds == null) {
    return "-"
  }
  const total = Math.max(0, Math.floor(seconds))
  const days = Math.floor(total / 86400)
  const hours = Math.floor((total % 86400) / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  if (days > 0) {
    return `${days}d ${hours}h`
  }
  if (hours > 0) {
    return `${hours}h ${minutes}m`
  }
  return `${minutes}m`
}

async function apiJson<T = unknown>(input: RequestInfo | URL, init?: RequestInit): Promise<T> {
  const response = await fetch(input, {
    credentials: "same-origin",
    ...init,
  })
  if (!response.ok) {
    let message = `HTTP ${response.status}`
    try {
      const data = (await response.json()) as { detail?: string }
      if (data.detail) {
        message = data.detail
      }
    } catch {
      // Keep the status-based message.
    }
    throw new Error(message)
  }
  return (await response.json()) as T
}

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof Error && error.message) {
    return error.message
  }
  return fallback
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

function pageFromPath(pathname: string): PageId {
  const normalized = pathname.replace(/\/+$/, "") || "/"
  const match = Object.entries(pagePaths).find(([, path]) => path === normalized)
  return match ? (match[0] as PageId) : "monitor"
}

export default App
