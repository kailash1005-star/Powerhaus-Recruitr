"use client"

import { ReactNode } from "react"
import { usePathname, useRouter } from "next/navigation"
import {
  Home,
  Settings,
  BarChart3,
  Sparkles,
  Zap,
  LayoutDashboard,
  LogOut,
  Bell,
  Search,
} from "lucide-react"
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"

interface AppShellProps {
  children: ReactNode
}

// ── Global sidebar navigation (fixed order, path-based) ──
const globalNavItems = [
  { id: "home", label: "Home", icon: "home" as const, path: "/" },
  { id: "search", label: "Search", icon: "search" as const, path: "/search-history" },
  { id: "results", label: "Results", icon: "results" as const, path: "/results" },
  { id: "dashboards", label: "Dashboards", icon: "dashboard" as const, path: "/dashboards" },
]

// ── Global user profile (consistent across all screens) ──
const globalUser = {
  name: "Alex Rivera",
  subtitle: "Premium Account",
  avatar: "https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?w=32&h=32&fit=crop&crop=face",
}

// ── Derive active nav from current pathname ──
function getActiveNavId(pathname: string): string {
  if (pathname === "/") return "home"
  if (pathname.startsWith("/search-history")) return "search"
  if (pathname.startsWith("/search")) return "search"
  if (pathname.startsWith("/runs")) return "search"
  if (pathname.startsWith("/processing")) return "search"
  if (pathname.startsWith("/results")) return "results"
  if (pathname.startsWith("/dashboards")) return "dashboards"
  if (pathname.startsWith("/workflows")) return "dashboards"
  return "home"
}

const iconMap = {
  home: Home,
  search: Search,
  results: Sparkles,
  dashboard: LayoutDashboard,
  analytics: BarChart3,
  automations: Zap,
  settings: Settings,
}

export function AppShell({ children }: AppShellProps) {
  const pathname = usePathname()
  const router = useRouter()

  const activeNavId = getActiveNavId(pathname)

  const handleNavClick = (item: (typeof globalNavItems)[0]) => {
    router.push(item.path)
  }


  return (
    <div className="flex min-h-screen bg-[#f7f9fb]">
      {/* ── Global Header ── */}
      <header className="fixed top-0 left-0 right-0 h-14 bg-white border-b border-[#e0e3e5] flex items-center justify-between px-6 z-50">
        {/* Left - Logo */}
        <div className="flex items-center gap-2">
          {/* <img src="/image.png" alt="Agamx Logo" className="w-8 h-8 rounded-lg object-contain" /> */}
          <span className="font-semibold text-[#191c1e]">LeadGen Agent</span>
        </div>

        {/* Right - Global User Profile */}
        <div className="flex items-center gap-4">
          <button className="relative text-[#565e74] hover:text-[#191c1e]">
            <Bell className="w-5 h-5" />
            <span className="absolute -top-1 -right-1 w-2 h-2 bg-red-500 rounded-full" />
          </button>
          <div className="flex items-center gap-3 border-l border-[#e0e3e5] pl-4">
            <div className="text-right hidden md:block">
              <p className="text-xs font-semibold text-[#191c1e] leading-none">{globalUser.name}</p>
              <p className="text-[10px] text-[#94a3b8]">{globalUser.subtitle}</p>
            </div>
            <Avatar className="w-8 h-8">
              <AvatarImage src={globalUser.avatar} />
              <AvatarFallback className="bg-[#0f172a] text-white text-xs">AR</AvatarFallback>
            </Avatar>
          </div>
        </div>
      </header>

      {/* ── Global Sidebar ── */}
      <aside className="fixed left-0 top-14 bottom-0 w-56 bg-[#0f172a] text-white flex flex-col z-40">
        {/* Brand */}
        <div className="p-4 border-b border-[#1e293b]">
          <h2 className="font-bold text-lg text-white">LeadGen AI</h2>
          <p className="text-[10px] uppercase tracking-widest text-[#64748b] mt-0.5">The Digital Curator</p>
        </div>

        {/* Navigation (fixed global order) */}
        <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
          {globalNavItems.map((item) => {
            const Icon = iconMap[item.icon]
            const isActive = item.id === activeNavId

            return (
              <button
                key={item.id}
                onClick={() => handleNavClick(item)}
                className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg transition-colors ${
                  isActive
                    ? "bg-[#0061ff] text-white"
                    : "text-[#94a3b8] hover:bg-[#1e293b] hover:text-white"
                }`}
              >
                <Icon className="w-4 h-4" />
                <span className="text-sm">{item.label}</span>
              </button>
            )
          })}
        </nav>

        {/* Bottom Links */}
        <div className="border-t border-[#1e293b] p-3 space-y-1">
          <button className="w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-[#94a3b8] hover:bg-[#1e293b] hover:text-white transition-colors">
            <LogOut className="w-4 h-4" />
            <span className="text-sm">Logout</span>
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 ml-56 mt-14">
        {children}
      </main>
    </div>
  )
}
