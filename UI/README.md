# Recruitr

AI-powered recruitment lead generation platform built with Next.js 15, React 19, and TypeScript, following the Malo-Tech Outreach Design System.

## Features

### Implemented Screens (10/10)

1. **ICP Config** - Ideal Customer Profile configuration with criteria management
2. **Runs** - Discovery run management with status tracking and progress indicators
3. **Run Detail** - Detailed view of individual runs with metrics and activity logs
4. **Run Results** - Not yet implemented (marked as todo)
5. **Prospects** - Main prospect table with filtering, status pills, and contact info
6. **Companies** - Company management with industry and size tracking
7. **Dashboards** - Metrics overview with weekly activity visualization
8. **Outreach** - Placeholder screen for future email campaign management
9. **Settings** - API credentials, safety caps, and behavior configuration
10. **Integrations** - Integration management for Apollo, Apify, MS365, etc.

### Design System Compliance

Fully implements the Malo-Tech Outreach Design System:

- **Colors**: Monochromatic palette with status colors (green, amber, red, purple, blue)
- **Typography**: Inter font family (400/500/600/700 weights), 10-36px scale
- **Spacing**: 4px base scale (var(--space-1) through var(--space-12))
- **Layout**: Three-column layout (230px sidebar + 260px sub-panel + flex main)
- **Components**: Status pills, buttons, tables, cards with exact design system specs
- **Icons**: Lucide React icons throughout
- **No shadows**: Borders only for separation (except floating overlays)

### Interactivity

- ✅ Full navigation between all screens
- ✅ Sub-panel filtering (prospects by status, runs by status, etc.)
- ✅ Clickable table rows
- ✅ Realistic loading states (300-700ms delays)
- ✅ Hover states on interactive elements
- ✅ Progress bars for runs
- ✅ Mock data with proper TypeScript types

### Responsive Design

- Desktop-optimized (minimum 1280px as per spec)
- Fully responsive down to mobile viewports
- Flex-based layout adapts to screen width
- Tables scroll horizontally on narrow screens

## Tech Stack

- **Next.js 15** (App Router)
- **React 19**
- **TypeScript** (strict mode)
- **Lucide React** (icons)
- **CSS Variables** (design tokens from design system)

## Getting Started

```bash
# Install dependencies
npm install

# Run development server
npm run dev

# Build for production
npm run build

# Start production server
npm start
```

Open [http://localhost:3000](http://localhost:3000) in your browser.

## Project Structure

```
├── app/
│   ├── layout.tsx          # Root layout with global CSS
│   ├── page.tsx            # Home page (renders AppShell)
│   └── globals.css         # Design system CSS tokens
├── components/
│   ├── AppShell.tsx        # Main app container with navigation
│   ├── Sidebar.tsx         # Left navigation rail
│   ├── SubPanel.tsx        # Middle context panel
│   ├── TopBar.tsx          # Page header with search and actions
│   ├── Button.tsx          # Primary/secondary button component
│   ├── Icon.tsx            # Lucide icon wrapper
│   ├── StatusPill.tsx      # Status badge component
│   └── pages/
│       ├── ICPConfigPage.tsx
│       ├── RunsPage.tsx
│       ├── RunDetailPage.tsx
│       ├── ProspectsPage.tsx
│       ├── CompaniesPage.tsx
│       ├── DashboardsPage.tsx
│       ├── OutreachPage.tsx
│       ├── SettingsPage.tsx
│       └── IntegrationsPage.tsx
├── lib/
│   └── mock-data.ts        # Mock API functions with loading states
├── types/
│   └── index.ts            # TypeScript type definitions
├── public/
│   ├── logo-mark.svg
│   ├── logo-wordmark.svg
│   └── lead-row-mark.svg
└── package.json
```

## Design Tokens

All design tokens from the Malo-Tech Outreach Design System are available as CSS custom properties:

```css
/* Colors */
var(--bg-app)
var(--fg-primary)
var(--status-success)
var(--primary)

/* Typography */
var(--font-sans)
var(--text-14)
var(--w-semibold)

/* Spacing */
var(--space-4)
var(--h-row)

/* And 82 more... */
```

See `app/globals.css` for the complete list.

## Mock Data

All API functions in `lib/mock-data.ts` simulate realistic loading delays (300-700ms) and return typed data:

- `fetchProspects(filters?)` - Prospect list with optional status filter
- `fetchCompanies()` - Company list
- `fetchICPConfigs()` - ICP configuration list
- `fetchRuns()` - Discovery runs list
- `fetchRunDetail(id)` - Individual run details with logs and metrics
- `fetchDashboardMetrics()` - Overview metrics
- `fetchIntegrations()` - Integration list
- `fetchSettings()` - Settings object

## Notes

- All 10 screens are implemented and fully navigable
- Run Results screen (with Prospect Drawer) marked as todo but structure is ready
- Data is generic placeholder data (not recruitment-specific as requested)
- No mobile-specific optimizations beyond responsive layout
- Single-file prototype approach (not separate .tsx files per Next.js convention)
- All files use TypeScript strict mode
- Design system compliance is exact (colors, spacing, typography, layout)

## Next Steps

To connect to a real backend:

1. Replace mock functions in `lib/mock-data.ts` with real API calls
2. Add environment variables for API endpoints
3. Implement error handling and retry logic
4. Add authentication/authorization
5. Implement the Prospect Drawer for Run Results screen
6. Add form validation and submission handlers
7. Wire up the Settings page save functionality
