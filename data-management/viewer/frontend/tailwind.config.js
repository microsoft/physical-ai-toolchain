/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        border: 'hsl(var(--border))',
        input: 'hsl(var(--input))',
        ring: 'hsl(var(--ring))',
        background: 'hsl(var(--background))',
        foreground: 'hsl(var(--foreground))',
        primary: {
          DEFAULT: 'hsl(var(--primary))',
          foreground: 'hsl(var(--primary-foreground))',
        },
        secondary: {
          DEFAULT: 'hsl(var(--secondary))',
          foreground: 'hsl(var(--secondary-foreground))',
        },
        destructive: {
          DEFAULT: 'hsl(var(--destructive))',
          foreground: 'hsl(var(--destructive-foreground))',
        },
        muted: {
          DEFAULT: 'hsl(var(--muted))',
          foreground: 'hsl(var(--muted-foreground))',
        },
        accent: {
          DEFAULT: 'hsl(var(--accent))',
          foreground: 'hsl(var(--accent-foreground))',
        },
        popover: {
          DEFAULT: 'hsl(var(--popover))',
          foreground: 'hsl(var(--popover-foreground))',
        },
        card: {
          DEFAULT: 'hsl(var(--card))',
          foreground: 'hsl(var(--card-foreground))',
        },
        status: {
          neutral: {
            DEFAULT: 'hsl(var(--status-neutral))',
            foreground: 'hsl(var(--status-neutral-foreground))',
            subtle: 'hsl(var(--status-neutral-subtle))',
            border: 'hsl(var(--status-neutral-border))',
          },
          info: {
            DEFAULT: 'hsl(var(--status-info))',
            foreground: 'hsl(var(--status-info-foreground))',
            subtle: 'hsl(var(--status-info-subtle))',
            border: 'hsl(var(--status-info-border))',
          },
          success: {
            DEFAULT: 'hsl(var(--status-success))',
            foreground: 'hsl(var(--status-success-foreground))',
            subtle: 'hsl(var(--status-success-subtle))',
            border: 'hsl(var(--status-success-border))',
          },
          warning: {
            DEFAULT: 'hsl(var(--status-warning))',
            foreground: 'hsl(var(--status-warning-foreground))',
            subtle: 'hsl(var(--status-warning-subtle))',
            border: 'hsl(var(--status-warning-border))',
          },
          danger: {
            DEFAULT: 'hsl(var(--status-danger))',
            foreground: 'hsl(var(--status-danger-foreground))',
            subtle: 'hsl(var(--status-danger-subtle))',
            border: 'hsl(var(--status-danger-border))',
          },
        },
        severity: {
          minor: 'hsl(var(--status-warning))',
          low: 'hsl(var(--status-warning))',
          major: 'hsl(var(--status-warning))',
          medium: 'hsl(var(--status-warning))',
          critical: 'hsl(var(--status-danger))',
          high: 'hsl(var(--status-danger))',
        },
        chart: {
          1: 'hsl(var(--chart-1))',
          2: 'hsl(var(--chart-2))',
          3: 'hsl(var(--chart-3))',
          4: 'hsl(var(--chart-4))',
          5: 'hsl(var(--chart-5))',
        },
      },
      borderRadius: {
        lg: 'var(--radius)',
        md: 'calc(var(--radius) - 2px)',
        sm: 'calc(var(--radius) - 4px)',
      },
    },
  },
  plugins: [],
}
