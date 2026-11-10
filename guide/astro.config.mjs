import starlight from "@astrojs/starlight";
import { defineConfig } from "astro/config";
import remarkMermaid from "./src/plugins/remark-mermaid.mjs";

// SITE and BASE come from the deploy, which serves the guide under guide/ of the repository's Pages site.
// The graph over logos-chat's main lives beside the guide on that site, so its link is a full address: one
// starting with / would take the guide's base. A build for no site, as `npm run dev`, has no page to link.
const guide = process.env.SITE && new URL(`${process.env.BASE ?? ""}/`, process.env.SITE);
const overMain = guide ? [{ label: "Over main", link: new URL("../logos-chat/", guide).href }] : [];

export default defineConfig({
  site: process.env.SITE,
  base: process.env.BASE ?? "/",
  markdown: { remarkPlugins: [remarkMermaid] },
  integrations: [
    starlight({
      title: "chat-sim",
      description: "Whether a group of logos-chat clients still holds one group when the network delays and loses what its members send: how chat-sim runs one, and how to read what it finds.",
      social: [{ icon: "github", label: "chat-sim", href: "https://github.com/logos-messaging/chat-sim" }],
      components: { MarkdownContent: "./src/components/MarkdownContent.astro" },
      expressiveCode: { defaultProps: { wrap: true } },
      customCss: ["@fontsource-variable/inter", "./src/styles/theme.css"],
      sidebar: [
        {
          label: "Guide",
          items: [
            { label: "Introduction", link: "/" },
            { slug: "how-it-works" },
            { slug: "run-it" },
            { slug: "read-the-report" },
            { slug: "simulations" },
            { slug: "reports" },
            ...overMain,
          ],
        },
        {
          label: "Reference",
          items: [{ slug: "reference/commands" }, { slug: "reference/records" }],
        },
      ],
    }),
  ],
});
