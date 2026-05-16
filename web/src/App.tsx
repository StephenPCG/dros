import { Activity, RefreshCw, Terminal } from "lucide-react"

import { Button } from "@/components/ui/button"

const statusRows = [
  ["CLI", "gw help available"],
  ["Daemon", "skeleton entrypoint"],
  ["Web API", "/api/health"],
]

function App() {
  return (
    <main className="min-h-svh bg-muted/40">
      <div className="mx-auto flex min-h-svh w-full max-w-5xl flex-col px-5 py-6">
        <header className="flex items-center justify-between gap-4 border-b pb-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-normal">DROS</h1>
            <p className="mt-1 text-sm text-muted-foreground">
              Personal Debian Router OS monitor shell.
            </p>
          </div>
          <Button variant="outline" size="icon" aria-label="Refresh status">
            <RefreshCw className="size-4" />
          </Button>
        </header>

        <section className="grid flex-1 content-start gap-4 py-6 md:grid-cols-[1.4fr_1fr]">
          <div className="rounded-lg border bg-card p-4">
            <div className="mb-4 flex items-center gap-2">
              <Activity className="size-4 text-primary" />
              <h2 className="text-base font-medium">System Status</h2>
            </div>
            <div className="divide-y">
              {statusRows.map(([name, value]) => (
                <div key={name} className="grid grid-cols-[8rem_1fr] gap-3 py-3 text-sm">
                  <span className="text-muted-foreground">{name}</span>
                  <span className="font-mono">{value}</span>
                </div>
              ))}
            </div>
          </div>

          <div className="rounded-lg border bg-card p-4">
            <div className="mb-4 flex items-center gap-2">
              <Terminal className="size-4 text-primary" />
              <h2 className="text-base font-medium">Daily Entry</h2>
            </div>
            <p className="text-sm leading-6 text-muted-foreground">
              Use the CLI for configuration changes. This Web shell starts with monitoring
              and troubleshooting tools.
            </p>
          </div>
        </section>
      </div>
    </main>
  )
}

export default App
