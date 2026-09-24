---
name: Enterprise Precision
colors:
  surface: '#f9f9ff'
  surface-dim: '#d7dae5'
  surface-bright: '#f9f9ff'
  surface-container-lowest: '#ffffff'
  surface-container-low: '#f1f3ff'
  surface-container: '#ebedf9'
  surface-container-high: '#e5e8f3'
  surface-container-highest: '#dfe2ed'
  on-surface: '#181c23'
  on-surface-variant: '#424655'
  inverse-surface: '#2c3039'
  inverse-on-surface: '#eef0fc'
  outline: '#727687'
  outline-variant: '#c2c6d8'
  surface-tint: '#0054d6'
  primary: '#004ec6'
  on-primary: '#ffffff'
  primary-container: '#0064fa'
  on-primary-container: '#f3f3ff'
  inverse-primary: '#b3c5ff'
  secondary: '#7431d3'
  on-secondary: '#ffffff'
  secondary-container: '#8e4fee'
  on-secondary-container: '#fffbff'
  tertiary: '#9e3300'
  on-tertiary: '#ffffff'
  tertiary-container: '#c74200'
  on-tertiary-container: '#fff2ee'
  error: '#ba1a1a'
  on-error: '#ffffff'
  error-container: '#ffdad6'
  on-error-container: '#93000a'
  primary-fixed: '#dae1ff'
  primary-fixed-dim: '#b3c5ff'
  on-primary-fixed: '#001849'
  on-primary-fixed-variant: '#003fa4'
  secondary-fixed: '#ecdcff'
  secondary-fixed-dim: '#d5baff'
  on-secondary-fixed: '#270057'
  on-secondary-fixed-variant: '#5e08bd'
  tertiary-fixed: '#ffdbcf'
  tertiary-fixed-dim: '#ffb59b'
  on-tertiary-fixed: '#380d00'
  on-tertiary-fixed-variant: '#822800'
  background: '#f9f9ff'
  on-background: '#181c23'
  surface-variant: '#dfe2ed'
typography:
  headline-xl:
    fontFamily: Inter
    fontSize: 32px
    fontWeight: '600'
    lineHeight: 40px
  headline-lg:
    fontFamily: Inter
    fontSize: 24px
    fontWeight: '600'
    lineHeight: 32px
  headline-md:
    fontFamily: Inter
    fontSize: 20px
    fontWeight: '600'
    lineHeight: 28px
  headline-sm:
    fontFamily: Inter
    fontSize: 16px
    fontWeight: '600'
    lineHeight: 24px
  title:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '600'
    lineHeight: 22px
  body-default:
    fontFamily: Inter
    fontSize: 14px
    fontWeight: '400'
    lineHeight: 22px
  body-compact:
    fontFamily: Inter
    fontSize: 13px
    fontWeight: '400'
    lineHeight: 20px
  label-default:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '400'
    lineHeight: 18px
  label-medium:
    fontFamily: Inter
    fontSize: 12px
    fontWeight: '500'
    lineHeight: 18px
  caption:
    fontFamily: Inter
    fontSize: 11px
    fontWeight: '400'
    lineHeight: 16px
rounded:
  sm: 0.125rem
  DEFAULT: 0.25rem
  md: 0.375rem
  lg: 0.5rem
  xl: 0.75rem
  full: 9999px
spacing:
  gutter: 16px
  gutter-compact: 12px
  margin: 24px
  margin-header: 16px
  space-xs: 4px
  space-sm: 8px
  space-md: 16px
  space-lg: 24px
  space-xl: 32px
---

## Brand & Style
This design system embodies modern enterprise utility: restrained, razor-sharp, lightweight, and structured for high-density B2B productivity. It prioritizes clarity over ornament, ensuring data analysis and multi-step enterprise workflows occur with minimal cognitive friction. 

The aesthetic is strictly **Corporate / Modern** anchored in flat tonal architecture:
- Visual integrity relies on structural discipline, 1px neutral dividers, and flat panels rather than decorative visuals.
- Strictly prohibit heavy dropshadows, glassmorphic blurs, ambient multi-stop linear gradients, 3D gimmicks, or exaggerated pill corners across dashboard cards and panels.
- Micro-interactions are snappy (100ms–150ms transitions), confident, and purposeful, reinforcing stability and trust for mission-critical operations.

## Colors
The color palette establishes rigorous functional contrast across data-dense panels, ensuring WCAG AA conformance and instant status recognition.

### Color Tokens & Assignments
- **Canvas / Background**: `#F7F8FA` provides a cool, low-fatigue backdrop for prolonged operational workflows.
- **Surface / Panel Background**: `#FFFFFF` provides clean contrast for data grids, summaries, and action panels.
- **Primary Accent (`#0064FA`)**: Reserved strictly for high-priority CTAs, active navigation items, input focus borders, and selected status indicators. Hover: `#247FFF`, Active: `#0051C9`.
- **AI / Intelligent Purple (`#722ED1`)**: Dedicated exclusively to generative AI capabilities, copilot panels, and automated workflow triggers. Surface tint: `#F9F0FF`, Border tint: `#D3ADF7`.
- **Text & Hierarchy**:
  - Primary Content / Headers: `#1D2129`
  - Secondary Content / Labels / Table Column Titles: `#4E5969`
  - Placeholder / Helper / Disabled Text: `#86909C`
- **Borders & Dividers**: `#E5E6EB` provides 1px structure for cards, table cell dividers, and navigation boundaries.
- **Semantic Functional States**:
  - Success: `#00B42A` (Background soft tint: `#E8FFEA`)
  - Warning: `#FF7D00` (Background soft tint: `#FFF7E8`)
  - Danger / Destructive: `#F53F3F` (Background soft tint: `#FFECE8`)

## Typography
The system enforces a singular, unyielding grotesque typography stack driven by **Inter** paired with system sans-serif fallback chains (`-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif`).

- **Tabular Figures**: Tabular numbers (`font-variant-numeric: tabular-nums`) must be enabled on all tables, metric widgets, monetary values, and operational counters.
- **Rhythm**: Default copy settles at 14px/22px for balance between readability and screen density. For ultra-dense data grids and nested tree-nodes, use the compact 13px/20px variation.
- **Weights**: Restrict weights strictly to 400 (Regular), 500 (Medium for tags/labels/table headers), and 600 (Semibold for titles and metric highlights). Avoid heavy weights (700+) to keep the UI light.

## Layout & Spacing
The layout is optimized for desktop enterprise web workstations (baseline viewport standard: 1600x1000px).

### Layout Blueprint
- **Global Header**: Fixed at the top, height exactly `56px`, horizontal padding `24px`, background `#FFFFFF`, bottom border `1px solid #E5E6EB`.
- **Sidebar Navigation**: Fixed along the left axis, width exactly `220px` (collapsible to `64px` icon-rail mode), right border `1px solid #E5E6EB`, background `#FFFFFF`.
- **Main Canvas**: Fluid horizontal fill with background `#F7F8FA`, strictly padded at `24px` (`margin`). Internal dashboard cards utilize `16px` gutters.

### 8px Spacing Hierarchy
All margins, paddings, and component compositions follow an explicit 8px increment matrix:
- `4px` (`space-xs`): Micro inline gaps, tag padding, icon-text gap.
- `8px` (`space-sm`): Input padding, button inner gap, dense table vertical padding.
- `16px` (`space-md`): Standard card body padding, filter bar gaps.
- `24px` (`space-lg`): Outer container canvas margin, major panel spacing.
- `32px` (`space-xl`): Section splits in settings or analytical viewports.

## Elevation & Depth
Depth in this system is flat and surface-driven. Visual organization is achieved primarily through `1px solid #E5E6EB` borders and surface contrasts (`#F7F8FA` vs `#FFFFFF`), deliberately bypassing heavy multi-layered shadows.

- **Base Cards & Surfaces**: Zero shadow. Boundary defined strictly by `border: 1px solid #E5E6EB` with background `#FFFFFF`.
- **Card Hover (Interactive list/grid items)**: `box-shadow: 0 2px 8px rgba(0, 0, 0, 0.04); border-color: #C9CDD4;`
- **Floating Overlays (Dropdowns, Popovers, Select menus)**: `box-shadow: 0 4px 12px rgba(0, 0, 0, 0.08); border: 1px solid #E5E6EB;`
- **High-Order Modals & Drawers**: `box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12);` paired with backdrop overlay `rgba(29, 33, 41, 0.6)`.
- **Prohibited**: Ambient colored shadows, neomorphic double shadows, outer glows, and heavy dark dropshadows.

## Shapes
Geometry is disciplined, architectural, and compact. 

- **Cards & Data Panels**: Fixed `8px` radius (`rounded-lg`). Cards must never use full rounding or sharp 0px corners.
- **Controls & Form Elements**: `4px` radius (`rounded-sm`) applied to standard buttons, input fields, select triggers, and dropdown menus. Checkboxes and inner control markers use `2px`.
- **Badges & Status Dots**: `9999px` (Pill/Circle) reserved exclusively for numeric count tags, user avatars, and status pills.
- **Tabs**: Line-based active indicators (2px bottom edge) or flat segmented tabs with `4px` corner radiuses.

## Components

### Buttons
- **Heights**: Primary control standard is `32px` (dense workflows) or `36px` (standard forms); compact actions at `28px`.
- **Primary**: Background `#0064FA`, text `#FFFFFF`, border `transparent`, radius `4px`. Hover `#247FFF`, active `#0051C9`.
- **Secondary / Default**: Background `#F2F3F5`, text `#1D2129`, border `transparent`. Hover `#E5E6EB`.
- **Outline**: Background `#FFFFFF`, text `#1D2129`, border `1px solid #E5E6EB`. Hover `#F7F8FA` with border `#C9CDD4`.
- **AI Action Button**: Background `#F9F0FF`, text `#722ED1`, border `1px solid #D3ADF7`. Hover background `#F0DCFF`.

### Form Inputs & Controls
- **Height**: Fixed at `32px` standard (36px for hero forms).
- **Surface**: Background `#FFFFFF`, border `1px solid #E5E6EB`, text `#1D2129`, placeholder `#86909C`, padding `0 12px`, radius `4px`.
- **States**: Hover border `#C9CDD4`. Focus border `#0064FA` with a subtle ring: `box-shadow: 0 0 0 2px rgba(0, 100, 250, 0.15)`. Error border `#F53F3F` with focus ring `rgba(245, 63, 63, 0.15)`.

### High-Density Data Tables
- **Header**: Background `#F7F8FA`, height `40px`, text `#4E5969`, font size `12px`, font weight `500`, bottom border `1px solid #E5E6EB`.
- **Rows**: Background `#FFFFFF`, cell height `44px` (dense mode `36px`), cell border bottom `1px solid #F2F3F5`. Row hover background `#F7F8FA`.
- **Typography**: Text `#1D2129` at `13px` or `14px`, numerical cells right-aligned with `font-variant-numeric: tabular-nums`.

### Tags & Status Chips
- **Dimensions**: Height `22px` or `24px`, padding `0 8px`, border radius `4px`, font size `12px`.
- **Success Tag**: Background `#E8FFEA`, text `#00B42A`, border `1px solid #B7EB8F`.
- **Warning Tag**: Background `#FFF7E8`, text `#FF7D00`, border `1px solid #FFD591`.
- **Danger Tag**: Background `#FFECE8`, text `#F53F3F`, border `1px solid #FFA39E`.
- **AI Tag**: Background `#F9F0FF`, text `#722ED1`, border `1px solid #D3ADF7`.

### Cards & Analytical Panels
- **Structure**: Background `#FFFFFF`, border `1px solid #E5E6EB`, border radius `8px`, zero shadow.
- **Card Header**: Height `48px`, padding `0 20px`, border bottom `1px solid #F2F3F5`, title font size `14px` (weight 600).
- **Card Body**: Padding `20px`.