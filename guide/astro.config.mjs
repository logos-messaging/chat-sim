import starlight from "@astrojs/starlight";
import { defineConfig } from "astro/config";
import starlightLinksValidator from "starlight-links-validator";
import remarkMermaid from "./src/plugins/remark-mermaid.mjs";

// The site serves the guide under guide/ of the repository's Pages site, and every link of the guide
// is written with that base, which the link checker holds to the pages and their headings.
const base = "/chat-sim/guide";
// SITE comes from the deploy. The graph over logos-chat's main lives beside the guide on that site, so
// its link is a full address: one starting with / would take the guide's base. A build for no site, as
// `npm run dev`, has no page to link.
const overMain = process.env.SITE
  ? [{ label: "Over main", link: new URL("../logos-chat/", new URL(`${base}/`, process.env.SITE)).href }]
  : [];

export default defineConfig({
  site: process.env.SITE,
  base,
  // Paths a link from outside the guide may hold, each sent on to the page that covers what it held.
  redirects: {
    "/how-it-works": `${base}/understand/how-a-run-works/`,
    "/run-it": `${base}/investigate/moved-run/`,
    "/read-the-report": `${base}/reference/commands/`,
  },
  markdown: { remarkPlugins: [remarkMermaid] },
  integrations: [
    starlight({
      title: "chat-sim",
      description: "Whether a group of logos-chat clients stays one group when the network delays and loses what its members send: how chat-sim runs one, how it judges a pull request, and how to take a run to your machine.",
      social: [{ icon: "github", label: "chat-sim", href: "https://github.com/logos-messaging/chat-sim" }],
      components: { MarkdownContent: "./src/components/MarkdownContent.astro" },
      expressiveCode: { defaultProps: { wrap: true } },
      customCss: ["@fontsource-variable/inter", "./src/styles/theme.css"],
      // The reports and the graph over main are pages of the site, not of the guide.
      plugins: [starlightLinksValidator({ exclude: ["/chat-sim/logos-chat/**", "/chat-sim/reports/**"] })],
      sidebar: [
        { label: "Introduction", link: "/" },
        {
          label: "Understand",
          items: [{ slug: "understand/how-a-run-works" }, { slug: "understand/scenarios" }],
        },
        {
          label: "Track",
          items: [{ slug: "track/pull-request" }, { slug: "track/main" }, { slug: "reports" }, ...overMain],
        },
        {
          label: "Investigate",
          items: [{ slug: "investigate/moved-run" }],
        },
        {
          label: "Reference",
          items: [
            { slug: "reference/scenario-format" },
            { slug: "reference/commands" },
            { slug: "reference/records" },
          ],
        },
      ],
    }),
  ],
});
