import { Card } from "@/components/ui/Card";
import { StatBlock } from "@/components/ui/StatBlock";
import { Badge } from "@/components/ui/Badge";
import { Button } from "@/components/ui/Button";
import { Gauge } from "@/components/ui/Gauge";

export default function StyleGuidePage() {
  return (
    <div className="min-h-screen bg-moat-bg p-8">
      <div className="mx-auto max-w-5xl space-y-8">
        <header>
          <h1 className="text-3xl font-bold text-moat-text">
            Moat Design System
          </h1>
          <p className="mt-1 text-moat-text-muted">
            Component library and style guide
          </p>
        </header>

        {/* StatBlock Section */}
        <section className="space-y-4">
          <h2 className="text-lg font-semibold text-moat-text">StatBlock</h2>
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-4">
            <Card>
              <StatBlock
                label="Current Price"
                value="$298.01"
                delta={{ value: "+18.3%", direction: "up" }}
              />
            </Card>
            <Card>
              <StatBlock
                label="Intrinsic Value"
                value="$231.44"
                delta={{ value: "-7.2%", direction: "down" }}
              />
            </Card>
            <Card>
              <StatBlock
                label="P/E Ratio"
                value="34.7x"
              />
            </Card>
            <Card>
              <StatBlock
                label="Free Cash Flow"
                value="$112.4B"
                delta={{ value: "+5.1%", direction: "up" }}
              />
            </Card>
          </div>
        </section>

        {/* Badge Section */}
        <section className="space-y-4">
          <h2 className="text-lg font-semibold text-moat-text">Badge</h2>
          <Card>
            <div className="flex flex-wrap items-center gap-3">
              <Badge variant="success">Undervalued</Badge>
              <Badge variant="danger">Overvalued</Badge>
              <Badge variant="warning">Fair Value</Badge>
              <Badge variant="neutral">Pending</Badge>
            </div>
          </Card>
        </section>

        {/* Button Section */}
        <section className="space-y-4">
          <h2 className="text-lg font-semibold text-moat-text">Button</h2>
          <Card>
            <div className="space-y-6">
              <div>
                <p className="mb-3 text-xs font-medium uppercase tracking-widest text-moat-text-muted">
                  Primary
                </p>
                <div className="flex flex-wrap items-center gap-3">
                  <Button size="sm">Small</Button>
                  <Button size="md">Medium</Button>
                  <Button size="lg">Large</Button>
                  <Button size="md" disabled>
                    Disabled
                  </Button>
                </div>
              </div>
              <div>
                <p className="mb-3 text-xs font-medium uppercase tracking-widest text-moat-text-muted">
                  Secondary
                </p>
                <div className="flex flex-wrap items-center gap-3">
                  <Button variant="secondary" size="sm">
                    Small
                  </Button>
                  <Button variant="secondary" size="md">
                    Medium
                  </Button>
                  <Button variant="secondary" size="lg">
                    Large
                  </Button>
                  <Button variant="secondary" size="md" disabled>
                    Disabled
                  </Button>
                </div>
              </div>
            </div>
          </Card>
        </section>

        {/* Card Hover Section */}
        <section className="space-y-4">
          <h2 className="text-lg font-semibold text-moat-text">
            Card (with hover)
          </h2>
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-3">
            <Card hover>
              <p className="text-sm text-moat-text-muted">
                Hover over this card to see the surface color shift.
              </p>
            </Card>
            <Card hover>
              <p className="text-sm text-moat-text-muted">
                Cards use rounded-2xl corners and a subtle border.
              </p>
            </Card>
            <Card hover>
              <p className="text-sm text-moat-text-muted">
                Standardized p-6 padding across all cards.
              </p>
            </Card>
          </div>
        </section>

        {/* Gauge Section */}
        <section className="space-y-4">
          <h2 className="text-lg font-semibold text-moat-text">Gauge</h2>
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-3">
            <Card>
              <div className="flex justify-center">
                <Gauge
                  value={-29.5}
                  min={-50}
                  max={50}
                  label="Margin of Safety"
                />
              </div>
            </Card>
            <Card>
              <div className="flex justify-center">
                <Gauge
                  value={15.2}
                  min={-50}
                  max={50}
                  label="Upside Potential"
                />
              </div>
            </Card>
            <Card>
              <div className="flex justify-center">
                <Gauge
                  value={42.0}
                  min={-50}
                  max={50}
                  label="Confidence"
                />
              </div>
            </Card>
          </div>
        </section>

        {/* Color Palette */}
        <section className="space-y-4">
          <h2 className="text-lg font-semibold text-moat-text">
            Color Palette
          </h2>
          <Card>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-5">
              {[
                { name: "bg", color: "bg-moat-bg" },
                { name: "surface", color: "bg-moat-surface" },
                { name: "border", color: "bg-moat-border" },
                { name: "accent", color: "bg-moat-accent" },
                { name: "accent-dim", color: "bg-moat-accent-dim" },
                { name: "danger", color: "bg-moat-danger" },
                { name: "warning", color: "bg-moat-warning" },
                { name: "text", color: "bg-moat-text" },
                { name: "text-muted", color: "bg-moat-text-muted" },
              ].map((swatch) => (
                <div key={swatch.name} className="flex flex-col items-center gap-2">
                  <div
                    className={`h-12 w-12 rounded-lg border border-moat-border ${swatch.color}`}
                  />
                  <span className="text-xs text-moat-text-muted">
                    {swatch.name}
                  </span>
                </div>
              ))}
            </div>
          </Card>
        </section>

        {/* Typography */}
        <section className="space-y-4">
          <h2 className="text-lg font-semibold text-moat-text">Typography</h2>
          <Card>
            <div className="space-y-4">
              <div>
                <p className="mb-1 text-xs font-medium uppercase tracking-widest text-moat-text-muted">
                  Body (Inter)
                </p>
                <p className="text-moat-text">
                  The quick brown fox jumps over the lazy dog.
                </p>
              </div>
              <div>
                <p className="mb-1 text-xs font-medium uppercase tracking-widest text-moat-text-muted">
                  Monospace Numbers (JetBrains Mono)
                </p>
                <p className="font-mono text-2xl text-moat-text">
                  $1,234.56 &middot; 42.7% &middot; 18.3x &middot; -$98.01
                </p>
              </div>
            </div>
          </Card>
        </section>
      </div>
    </div>
  );
}
