import { ReactNode } from "react";
import { Box, Container } from "@chakra-ui/react";

import { MOBILE_TAB_HEIGHT } from "./Shell";

/**
 * Pin a child Composer at the bottom of the viewport, above the mobile tab
 * bar. Designed for the planner / ad-hoc job composers that several pages
 * use. Width matches the page's content container.
 */
export function StickyComposer({ children }: { children: ReactNode }) {
  return (
    <Box
      position="fixed"
      left={0}
      right={0}
      zIndex={15}
      bg="bg.subtle"
      borderTopWidth="1px"
      borderColor="border.subtle"
      bottom={{
        base: `calc(${MOBILE_TAB_HEIGHT}px + env(safe-area-inset-bottom))`,
        md: 0,
      }}
    >
      <Container
        maxW={{ base: "100%", md: "85ch" }}
        px={{ base: 0, md: 6 }}
      >
        {children}
      </Container>
    </Box>
  );
}
