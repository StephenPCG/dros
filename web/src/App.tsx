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
  ScrollText,
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

type PageId = "monitor" | "tools" | "logs" | "openvpn"
type MonitorViewId = "overview" | "bandwidth" | "ping"
type LogsViewId = "invocations" | "errors"
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

type MonitorTimespan = {
  id: string
  label: string
  seconds: number
}

type MonitorRrdTargets = {
  timespans: MonitorTimespan[]
  bandwidth: Array<{
    name: string
    hasData: boolean
  }>
  ping: Array<{
    name: string
    hasLatency: boolean
    hasLoss: boolean
  }>
}

type BandwidthPoint = {
  timestamp: number
  rxBitsPerSecond: number | null
  txBitsPerSecond: number | null
}

type BandwidthSeries = {
  target: string
  timespan: string
  unit: string
  points: BandwidthPoint[]
}

type PingPoint = {
  timestamp: number
  latencyMs: number | null
  lossPercent: number | null
}

type PingSeries = {
  target: string
  timespan: string
  latencyUnit: string
  lossUnit: string
  points: PingPoint[]
}

type LogRecord = {
  ts?: number
  kind?: string
  channel?: string
  phase?: string
  argv?: string[]
  event?: string
  iface?: string
  message?: string
  errorType?: string
  exitCode?: number
  durationMs?: number
  pid?: number
}

const pages: Array<{
  id: PageId
  label: string
  icon: LucideIcon
}> = [
  { id: "monitor", label: "监控", icon: Activity },
  { id: "tools", label: "工具", icon: Wrench },
  { id: "logs", label: "日志", icon: ScrollText },
  { id: "openvpn", label: "OpenVPN", icon: Shield },
]

const pagePaths: Record<PageId, string> = {
  monitor: "/monitor",
  tools: "/tools",
  logs: "/logs",
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
        ) : activePage === "logs" ? (
          <LogsPage />
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
  const [view, setView] = useState<MonitorViewId>(() => monitorViewFromPath(window.location.pathname))

  useEffect(() => {
    function handlePopState() {
      setView(monitorViewFromPath(window.location.pathname))
    }
    window.addEventListener("popstate", handlePopState)
    return () => window.removeEventListener("popstate", handlePopState)
  }, [])

  function handleViewChange(next: MonitorViewId) {
    setView(next)
    const path = monitorViewPath(next)
    if (window.location.pathname !== path) {
      window.history.pushState({ page: "monitor", view: next }, "", path)
    }
  }

  return (
    <div className="min-h-[calc(100svh-11rem)] space-y-5">
      <MonitorSubNavigation view={view} onChange={handleViewChange} />
      {view === "bandwidth" ? (
        <MonitorBandwidthPage />
      ) : view === "ping" ? (
        <MonitorPingPage />
      ) : (
        <MonitorOverviewPage />
      )}
    </div>
  )
}

function MonitorSubNavigation({
  view,
  onChange,
}: {
  view: MonitorViewId
  onChange: (view: MonitorViewId) => void
}) {
  const items: Array<{ id: MonitorViewId; label: string }> = [
    { id: "overview", label: "概览" },
    { id: "bandwidth", label: "带宽" },
    { id: "ping", label: "Ping" },
  ]
  return (
    <div className="flex max-w-full gap-1 overflow-x-auto rounded-md border bg-muted p-1">
      {items.map((item) => (
        <button
          key={item.id}
          className={cn(
            "h-9 min-w-fit rounded-sm px-3 text-sm font-medium transition-colors",
            view === item.id
              ? "bg-background text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
          type="button"
          onClick={() => onChange(item.id)}
          aria-current={view === item.id ? "page" : undefined}
        >
          {item.label}
        </button>
      ))}
    </div>
  )
}

function MonitorOverviewPage() {
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
    <div className="space-y-5">
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

function MonitorBandwidthPage() {
  const [targets, setTargets] = useState<MonitorRrdTargets | null>(null)
  const [selectedTargets, setSelectedTargets] = useState<string[]>([])
  const [selectedTimespans, setSelectedTimespans] = useState<string[]>([])
  const [series, setSeries] = useState<Record<string, BandwidthSeries>>({})
  const [loading, setLoading] = useState(true)
  const [loadingSeries, setLoadingSeries] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    let cancelled = false
    async function loadTargets() {
      setLoading(true)
      setError("")
      try {
        const data = await apiJson<MonitorRrdTargets>("/api/monitor/rrd/targets")
        if (cancelled) {
          return
        }
        setTargets(data)
        setSelectedTargets((current) => keepSelected(current, data.bandwidth.map((item) => item.name), 1))
        setSelectedTimespans((current) => keepSelected(current, data.timespans.map((item) => item.id), 1))
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err, "加载带宽目标失败"))
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }
    loadTargets()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!targets || selectedTargets.length === 0 || selectedTimespans.length === 0) {
      setSeries({})
      return
    }
    let cancelled = false
    async function loadSeries() {
      setLoadingSeries(true)
      setError("")
      try {
        const pairs = selectedTargets.flatMap((target) =>
          selectedTimespans.map((timespan) => ({ target, timespan })),
        )
        const values = await Promise.all(
          pairs.map(async (pair) => {
            const params = new URLSearchParams({ target: pair.target, timespan: pair.timespan })
            const data = await apiJson<BandwidthSeries>(`/api/monitor/rrd/bandwidth?${params}`)
            return [matrixKey(pair.target, pair.timespan), data] as const
          }),
        )
        if (!cancelled) {
          setSeries(Object.fromEntries(values))
        }
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err, "加载带宽图数据失败"))
        }
      } finally {
        if (!cancelled) {
          setLoadingSeries(false)
        }
      }
    }
    loadSeries()
    return () => {
      cancelled = true
    }
  }, [targets, selectedTargets, selectedTimespans])

  const bandwidthTargets = targets?.bandwidth ?? []
  const timespans = targets?.timespans ?? []
  return (
    <MonitorGraphSection
      title="带宽"
      loading={loading}
      loadingSeries={loadingSeries}
      error={error}
      empty={bandwidthTargets.length === 0}
      emptyText="暂无 collectd interface RRD 数据"
      targetLabel="接口"
      targets={bandwidthTargets.map((item) => item.name)}
      timespans={timespans}
      selectedTargets={selectedTargets}
      selectedTimespans={selectedTimespans}
      onTargetsChange={setSelectedTargets}
      onTimespansChange={setSelectedTimespans}
      renderChart={(target, timespan) => (
        <BandwidthChart series={series[matrixKey(target, timespan)]} />
      )}
    />
  )
}

function MonitorPingPage() {
  const [targets, setTargets] = useState<MonitorRrdTargets | null>(null)
  const [selectedTargets, setSelectedTargets] = useState<string[]>([])
  const [selectedTimespans, setSelectedTimespans] = useState<string[]>([])
  const [series, setSeries] = useState<Record<string, PingSeries>>({})
  const [loading, setLoading] = useState(true)
  const [loadingSeries, setLoadingSeries] = useState(false)
  const [error, setError] = useState("")

  useEffect(() => {
    let cancelled = false
    async function loadTargets() {
      setLoading(true)
      setError("")
      try {
        const data = await apiJson<MonitorRrdTargets>("/api/monitor/rrd/targets")
        if (cancelled) {
          return
        }
        setTargets(data)
        setSelectedTargets((current) => keepSelected(current, data.ping.map((item) => item.name), 1))
        setSelectedTimespans((current) => keepSelected(current, data.timespans.map((item) => item.id), 1))
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err, "加载 Ping 目标失败"))
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }
    loadTargets()
    return () => {
      cancelled = true
    }
  }, [])

  useEffect(() => {
    if (!targets || selectedTargets.length === 0 || selectedTimespans.length === 0) {
      setSeries({})
      return
    }
    let cancelled = false
    async function loadSeries() {
      setLoadingSeries(true)
      setError("")
      try {
        const pairs = selectedTargets.flatMap((target) =>
          selectedTimespans.map((timespan) => ({ target, timespan })),
        )
        const values = await Promise.all(
          pairs.map(async (pair) => {
            const params = new URLSearchParams({ target: pair.target, timespan: pair.timespan })
            const data = await apiJson<PingSeries>(`/api/monitor/rrd/ping?${params}`)
            return [matrixKey(pair.target, pair.timespan), data] as const
          }),
        )
        if (!cancelled) {
          setSeries(Object.fromEntries(values))
        }
      } catch (err) {
        if (!cancelled) {
          setError(errorMessage(err, "加载 Ping 图数据失败"))
        }
      } finally {
        if (!cancelled) {
          setLoadingSeries(false)
        }
      }
    }
    loadSeries()
    return () => {
      cancelled = true
    }
  }, [targets, selectedTargets, selectedTimespans])

  const pingTargets = targets?.ping ?? []
  const timespans = targets?.timespans ?? []
  return (
    <MonitorGraphSection
      title="Ping"
      loading={loading}
      loadingSeries={loadingSeries}
      error={error}
      empty={pingTargets.length === 0}
      emptyText="暂无 collectd ping RRD 数据"
      targetLabel="目标"
      targets={pingTargets.map((item) => item.name)}
      timespans={timespans}
      selectedTargets={selectedTargets}
      selectedTimespans={selectedTimespans}
      onTargetsChange={setSelectedTargets}
      onTimespansChange={setSelectedTimespans}
      renderChart={(target, timespan) => <PingChart series={series[matrixKey(target, timespan)]} />}
    />
  )
}

function MonitorGraphSection({
  title,
  loading,
  loadingSeries,
  error,
  empty,
  emptyText,
  targetLabel,
  targets,
  timespans,
  selectedTargets,
  selectedTimespans,
  onTargetsChange,
  onTimespansChange,
  renderChart,
}: {
  title: string
  loading: boolean
  loadingSeries: boolean
  error: string
  empty: boolean
  emptyText: string
  targetLabel: string
  targets: string[]
  timespans: MonitorTimespan[]
  selectedTargets: string[]
  selectedTimespans: string[]
  onTargetsChange: (value: string[]) => void
  onTimespansChange: (value: string[]) => void
  renderChart: (target: string, timespan: string) => ReactNode
}) {
  if (loading) {
    return (
      <div className="grid h-40 place-items-center rounded-md border text-muted-foreground">
        <Loader2 className="size-5 animate-spin" aria-label="Loading" />
      </div>
    )
  }
  return (
    <div className="space-y-4">
      <div className="flex flex-col gap-3 rounded-md border bg-background p-3">
        <div className="flex items-center justify-between gap-3">
          <div className="text-sm font-medium">{title}</div>
          {loadingSeries ? <Loader2 className="size-4 animate-spin text-muted-foreground" /> : null}
        </div>
        <MultiSelectChips
          label={targetLabel}
          options={targets}
          selected={selectedTargets}
          onChange={onTargetsChange}
        />
        <MultiSelectChips
          label="时间段"
          options={timespans.map((item) => item.id)}
          optionLabels={Object.fromEntries(timespans.map((item) => [item.id, item.label]))}
          selected={selectedTimespans}
          onChange={onTimespansChange}
        />
      </div>
      {error ? (
        <div className="rounded-md border border-destructive/35 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      ) : null}
      {empty ? (
        <div className="rounded-md border border-dashed px-3 py-10 text-sm text-muted-foreground">
          {emptyText}
        </div>
      ) : selectedTargets.length === 0 || selectedTimespans.length === 0 ? (
        <div className="rounded-md border border-dashed px-3 py-10 text-sm text-muted-foreground">
          请选择至少一个{targetLabel}和时间段
        </div>
      ) : (
        <div className="max-w-full overflow-x-auto rounded-md border bg-background">
          <div
            className="grid min-w-max"
            style={{
              gridTemplateColumns: `10rem repeat(${selectedTimespans.length}, minmax(20rem, 1fr))`,
            }}
          >
            <div className="sticky left-0 z-20 border-b border-r bg-muted/80 px-3 py-2 text-xs font-medium text-muted-foreground">
              {targetLabel}
            </div>
            {selectedTimespans.map((timespan) => (
              <div
                key={timespan}
                className="border-b border-r bg-muted/50 px-3 py-2 text-xs font-medium text-muted-foreground last:border-r-0"
              >
                {timespans.find((item) => item.id === timespan)?.label ?? timespan}
              </div>
            ))}
            {selectedTargets.map((target) => (
              <div key={target} className="contents">
                <div className="sticky left-0 z-10 border-r border-t bg-background px-3 py-4 font-mono text-xs">
                  <div className="truncate">{target}</div>
                </div>
                {selectedTimespans.map((timespan) => (
                  <div key={`${target}:${timespan}`} className="border-r border-t p-3 last:border-r-0">
                    {renderChart(target, timespan)}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}

function MultiSelectChips({
  label,
  options,
  optionLabels,
  selected,
  onChange,
}: {
  label: string
  options: string[]
  optionLabels?: Record<string, string>
  selected: string[]
  onChange: (value: string[]) => void
}) {
  return (
    <div className="space-y-2">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div className="flex flex-wrap gap-2">
        {options.length === 0 ? (
          <span className="text-sm text-muted-foreground">暂无可选项</span>
        ) : (
          options.map((option) => {
            const checked = selected.includes(option)
            return (
              <label
                key={option}
                className={cn(
                  "inline-flex h-8 cursor-pointer items-center gap-2 rounded-md border px-2 text-sm transition-colors",
                  checked ? "border-ring bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
                )}
              >
                <input
                  className="size-3.5 accent-primary"
                  type="checkbox"
                  checked={checked}
                  onChange={() => onChange(toggleSelection(selected, option))}
                />
                <span>{optionLabels?.[option] ?? option}</span>
              </label>
            )
          })
        )}
      </div>
    </div>
  )
}

function BandwidthChart({ series }: { series?: BandwidthSeries }) {
  const points = series?.points ?? []
  const max = maxValue(points, [(point) => point.rxBitsPerSecond, (point) => point.txBitsPerSecond])
  return (
    <LineChartPanel
      emptyText="暂无带宽数据"
      points={points}
      leftLabel="bit/s"
      rightLabel=""
      maxLeft={max}
      maxRight={null}
      series={[
        {
          label: "RX",
          color: "#2563eb",
          value: (point) => point.rxBitsPerSecond,
          format: formatBitRate,
        },
        {
          label: "TX",
          color: "#dc2626",
          value: (point) => point.txBitsPerSecond,
          format: formatBitRate,
        },
      ]}
    />
  )
}

function PingChart({ series }: { series?: PingSeries }) {
  const points = series?.points ?? []
  const latencyMax = maxValue(points, [(point) => point.latencyMs])
  const lossMax = Math.max(100, maxValue(points, [(point) => point.lossPercent]))
  return (
    <LineChartPanel
      emptyText="暂无 Ping 数据"
      points={points}
      leftLabel="延迟 ms"
      rightLabel="丢包率 %"
      maxLeft={latencyMax}
      maxRight={lossMax}
      series={[
        {
          label: "延迟",
          color: "#2563eb",
          value: (point) => point.latencyMs,
          axis: "left",
          format: (value) => `${value.toFixed(1)} ms`,
        },
        {
          label: "丢包",
          color: "#dc2626",
          value: (point) => point.lossPercent,
          axis: "right",
          format: (value) => `${value.toFixed(1)}%`,
        },
      ]}
    />
  )
}

function LineChartPanel<Point extends { timestamp: number }>({
  points,
  series,
  emptyText,
  leftLabel,
  rightLabel,
  maxLeft,
  maxRight,
}: {
  points: Point[]
  series: Array<{
    label: string
    color: string
    value: (point: Point) => number | null
    axis?: "left" | "right"
    format: (value: number) => string
  }>
  emptyText: string
  leftLabel: string
  rightLabel: string
  maxLeft: number
  maxRight: number | null
}) {
  if (points.length === 0) {
    return <div className="grid h-48 place-items-center text-sm text-muted-foreground">{emptyText}</div>
  }
  const width = 480
  const height = 230
  const padding = { top: 18, right: rightLabel ? 54 : 20, bottom: 34, left: 54 }
  const minTimestamp = Math.min(...points.map((point) => point.timestamp))
  const maxTimestamp = Math.max(...points.map((point) => point.timestamp))
  const safeMaxLeft = maxLeft > 0 ? maxLeft : 1
  const safeMaxRight = maxRight && maxRight > 0 ? maxRight : safeMaxLeft
  const x = (timestamp: number) => {
    if (maxTimestamp === minTimestamp) {
      return padding.left
    }
    return (
      padding.left +
      ((timestamp - minTimestamp) / (maxTimestamp - minTimestamp)) *
        (width - padding.left - padding.right)
    )
  }
  const yLeft = (value: number) =>
    padding.top +
    (1 - value / safeMaxLeft) * (height - padding.top - padding.bottom)
  const yRight = (value: number) =>
    padding.top +
    (1 - value / safeMaxRight) * (height - padding.top - padding.bottom)
  const startLabel = formatChartTime(minTimestamp)
  const endLabel = formatChartTime(maxTimestamp)

  return (
    <div className="space-y-2">
      <svg className="h-56 w-full overflow-visible" viewBox={`0 0 ${width} ${height}`} role="img">
        <line
          x1={padding.left}
          x2={width - padding.right}
          y1={height - padding.bottom}
          y2={height - padding.bottom}
          stroke="currentColor"
          className="text-border"
        />
        <line
          x1={padding.left}
          x2={padding.left}
          y1={padding.top}
          y2={height - padding.bottom}
          stroke="currentColor"
          className="text-border"
        />
        {rightLabel ? (
          <line
            x1={width - padding.right}
            x2={width - padding.right}
            y1={padding.top}
            y2={height - padding.bottom}
            stroke="currentColor"
            className="text-border"
          />
        ) : null}
        <text x={padding.left - 8} y={padding.top + 4} textAnchor="end" className="fill-muted-foreground text-[11px]">
          {formatAxisValue(safeMaxLeft, series.find((item) => item.axis !== "right")?.format)}
        </text>
        <text
          x={padding.left - 8}
          y={height - padding.bottom}
          textAnchor="end"
          className="fill-muted-foreground text-[11px]"
        >
          0
        </text>
        {rightLabel ? (
          <>
            <text
              x={width - padding.right + 8}
              y={padding.top + 4}
              textAnchor="start"
              className="fill-muted-foreground text-[11px]"
            >
              {formatAxisValue(safeMaxRight, series.find((item) => item.axis === "right")?.format)}
            </text>
            <text
              x={width - padding.right + 8}
              y={height - padding.bottom}
              textAnchor="start"
              className="fill-muted-foreground text-[11px]"
            >
              0
            </text>
          </>
        ) : null}
        <text x={padding.left} y={height - 8} textAnchor="start" className="fill-muted-foreground text-[11px]">
          {startLabel}
        </text>
        <text
          x={width - padding.right}
          y={height - 8}
          textAnchor="end"
          className="fill-muted-foreground text-[11px]"
        >
          {endLabel}
        </text>
        <text x={padding.left} y={12} textAnchor="start" className="fill-muted-foreground text-[11px]">
          {leftLabel}
        </text>
        {rightLabel ? (
          <text x={width - padding.right} y={12} textAnchor="end" className="fill-muted-foreground text-[11px]">
            {rightLabel}
          </text>
        ) : null}
        {series.map((item) => (
          <path
            key={item.label}
            d={linePath(points, x, item.axis === "right" ? yRight : yLeft, item.value)}
            fill="none"
            stroke={item.color}
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth="2"
          />
        ))}
      </svg>
      <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
        {series.map((item) => (
          <span key={item.label} className="inline-flex items-center gap-1">
            <span className="size-2 rounded-full" style={{ background: item.color }} />
            {item.label}
          </span>
        ))}
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

function LogsPage() {
  const [view, setView] = useState<LogsViewId>("invocations")
  const [records, setRecords] = useState<LogRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")

  useEffect(() => {
    loadLogs(view)
  }, [view])

  async function loadLogs(nextView = view) {
    setLoading(true)
    setError("")
    try {
      const data = await apiJson<{ records: LogRecord[] }>(`/api/logs/${nextView}?limit=300`)
      setRecords(data.records)
    } catch (err) {
      setError(errorMessage(err, "加载日志失败"))
      setRecords([])
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-[calc(100svh-11rem)] space-y-4">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="grid grid-cols-2 rounded-md border bg-muted p-1 md:w-fit">
          {([
            ["invocations", "执行日志"],
            ["errors", "错误日志"],
          ] as const).map(([id, label]) => (
            <button
              key={id}
              className={cn(
                "h-9 rounded-sm px-3 text-sm font-medium transition-colors",
                view === id ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
              )}
              type="button"
              onClick={() => setView(id)}
              aria-current={view === id ? "page" : undefined}
            >
              {label}
            </button>
          ))}
        </div>
        <Button variant="outline" onClick={() => loadLogs()} disabled={loading}>
          {loading ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
          刷新
        </Button>
      </div>

      {error ? (
        <div className="rounded-md border border-destructive/35 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </div>
      ) : null}

      <div className="overflow-hidden rounded-md border bg-background">
        {loading ? (
          <div className="grid h-36 place-items-center text-muted-foreground">
            <Loader2 className="size-5 animate-spin" aria-label="Loading" />
          </div>
        ) : records.length === 0 ? (
          <div className="px-3 py-10 text-sm text-muted-foreground">暂无日志</div>
        ) : (
          <div className="max-w-full overflow-x-auto">
            <table className="w-full min-w-[58rem] text-left text-sm">
              <thead className="border-b bg-muted/50 text-xs uppercase text-muted-foreground">
                <tr>
                  <th className="sticky left-0 z-20 w-44 min-w-44 border-r bg-muted px-3 py-2 font-medium">
                    时间
                  </th>
                  <th className="px-3 py-2 font-medium">类型</th>
                  <th className="px-3 py-2 font-medium">命令 / Hook</th>
                  <th className="px-3 py-2 font-medium">状态</th>
                  <th className="px-3 py-2 font-medium">信息</th>
                </tr>
              </thead>
              <tbody className="divide-y">
                {records.map((record, index) => (
                  <tr key={`${record.ts ?? index}:${record.pid ?? ""}:${index}`}>
                    <td className="sticky left-0 z-10 w-44 min-w-44 border-r bg-background px-3 py-3 font-mono text-xs">
                      {formatLogTime(record.ts)}
                    </td>
                    <td className="px-3 py-3">
                      <span className="inline-flex h-7 items-center rounded-md border px-2 font-mono text-xs">
                        {record.channel ?? record.kind ?? "-"}
                      </span>
                    </td>
                    <td className="px-3 py-3 font-mono text-xs">
                      <div className="max-w-[28rem] truncate">{formatLogCommand(record)}</div>
                      {record.iface ? (
                        <div className="mt-1 text-muted-foreground">iface={record.iface}</div>
                      ) : null}
                    </td>
                    <td className="px-3 py-3 font-mono text-xs">{formatLogStatus(record)}</td>
                    <td className="px-3 py-3 text-xs">
                      <div className="max-w-[30rem] break-words">{formatLogMessage(record)}</div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
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
  const [downloadProfileTarget, setDownloadProfileTarget] = useState<OpenVPNProfile | null>(null)
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
    if (profile.kind === "client") {
      if (profile.outputFiles.length === 0) {
        setError("该 client 暂无可下载配置文件")
        return
      }
      setError("")
      setDownloadProfileTarget(profile)
      return
    }
    setError("")
    downloadProfileFile(profile)
  }

  function downloadProfileFile(profile: OpenVPNProfile, outputFile?: string) {
    if (!selectedInstance) {
      return
    }
    const params = outputFile ? `?${new URLSearchParams({ file: fileName(outputFile) }).toString()}` : ""
    setDownloadProfileTarget(null)
    window.location.assign(
      `/api/openvpn/instances/${encodeURIComponent(selectedInstance)}/profiles/${profile.kind}/${encodeURIComponent(profile.name)}/download${params}`,
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

      {downloadProfileTarget ? (
        <Modal title={`${downloadProfileTarget.name} 配置文件`} onClose={() => setDownloadProfileTarget(null)}>
          <div className="divide-y rounded-md border">
            {downloadProfileTarget.outputFiles.map((outputFile) => (
              <button
                key={outputFile}
                className="flex w-full items-center justify-between gap-3 px-3 py-3 text-left transition-colors hover:bg-muted"
                type="button"
                onClick={() => downloadProfileFile(downloadProfileTarget, outputFile)}
              >
                <span className="min-w-0">
                  <span className="block truncate font-mono text-sm">{fileName(outputFile)}</span>
                  <span className="mt-1 block truncate text-xs text-muted-foreground">{outputFile}</span>
                </span>
                <Download className="size-4 shrink-0 text-muted-foreground" />
              </button>
            ))}
          </div>
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

function keepSelected(current: string[], available: string[], fallbackCount: number): string[] {
  const kept = current.filter((item) => available.includes(item))
  if (kept.length > 0) {
    return kept
  }
  return available.slice(0, fallbackCount)
}

function toggleSelection(selected: string[], value: string): string[] {
  if (selected.includes(value)) {
    return selected.filter((item) => item !== value)
  }
  return [...selected, value]
}

function matrixKey(target: string, timespan: string): string {
  return `${target}\u0000${timespan}`
}

function maxValue<Point>(
  points: Point[],
  selectors: Array<(point: Point) => number | null>,
): number {
  let max = 0
  for (const point of points) {
    for (const selector of selectors) {
      const value = selector(point)
      if (value != null && Number.isFinite(value)) {
        max = Math.max(max, value)
      }
    }
  }
  return max
}

function linePath<Point extends { timestamp: number }>(
  points: Point[],
  x: (timestamp: number) => number,
  y: (value: number) => number,
  value: (point: Point) => number | null,
): string {
  let path = ""
  let drawing = false
  for (const point of points) {
    const current = value(point)
    if (current == null || !Number.isFinite(current)) {
      drawing = false
      continue
    }
    const command = drawing ? "L" : "M"
    path += `${command}${x(point.timestamp).toFixed(2)},${y(current).toFixed(2)}`
    drawing = true
  }
  return path
}

function formatAxisValue(value: number, formatter?: (value: number) => string): string {
  if (!formatter) {
    return value.toFixed(1)
  }
  return formatter(value)
}

function formatBitRate(bitsPerSecond: number): string {
  const units = ["bps", "Kbps", "Mbps", "Gbps", "Tbps"]
  let value = bitsPerSecond
  for (const unit of units) {
    if (Math.abs(value) < 1000 || unit === units[units.length - 1]) {
      return unit === "bps" ? `${Math.round(value)} ${unit}` : `${value.toFixed(1)} ${unit}`
    }
    value /= 1000
  }
  return `${Math.round(bitsPerSecond)} bps`
}

function formatChartTime(timestamp: number): string {
  return new Date(timestamp * 1000).toLocaleString(undefined, {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  })
}

function formatLogTime(timestamp: number | undefined): string {
  if (timestamp == null) {
    return "-"
  }
  return new Date(timestamp * 1000).toLocaleString()
}

function formatLogCommand(record: LogRecord): string {
  if (record.argv && record.argv.length > 0) {
    return record.argv.join(" ")
  }
  if (record.event) {
    return record.iface ? `${record.event} ${record.iface}` : record.event
  }
  return "-"
}

function formatLogStatus(record: LogRecord): string {
  const parts: string[] = []
  if (record.phase) {
    parts.push(record.phase)
  }
  if (record.exitCode != null) {
    parts.push(`exit=${record.exitCode}`)
  }
  if (record.durationMs != null) {
    parts.push(`${record.durationMs}ms`)
  }
  return parts.join(" · ") || "-"
}

function formatLogMessage(record: LogRecord): string {
  if (record.errorType && record.message) {
    return `${record.errorType}: ${record.message}`
  }
  return record.message ?? "-"
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
  if (normalized === "/monitor" || normalized.startsWith("/monitor/")) {
    return "monitor"
  }
  const match = Object.entries(pagePaths).find(([, path]) => path === normalized)
  return match ? (match[0] as PageId) : "monitor"
}

function monitorViewFromPath(pathname: string): MonitorViewId {
  const normalized = pathname.replace(/\/+$/, "") || "/"
  if (normalized === "/monitor/bandwidth") {
    return "bandwidth"
  }
  if (normalized === "/monitor/ping") {
    return "ping"
  }
  return "overview"
}

function monitorViewPath(view: MonitorViewId): string {
  if (view === "bandwidth") {
    return "/monitor/bandwidth"
  }
  if (view === "ping") {
    return "/monitor/ping"
  }
  return "/monitor"
}

export default App
