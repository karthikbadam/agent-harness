import { ReactNode } from "react";
import { Link as RouterLink, useLocation } from "react-router-dom";
import { Box, Container, Flex, HStack, Heading, Link, Text } from "@chakra-ui/react";

interface ShellProps {
  title?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
}

const TABS = [
  { to: "/jobs", label: "Jobs" },
  { to: "/schedules", label: "Schedules" },
  { to: "/settings", label: "Settings" },
];

export function Shell({ title, right, children }: ShellProps) {
  const loc = useLocation();
  return (
    <Flex direction="column" minH="100dvh">
      <Box
        as="header"
        position="sticky"
        top={0}
        zIndex={1}
        bg="bg"
        borderBottomWidth="1px"
        px={4}
        py={3}
        pt="max(env(safe-area-inset-top), 12px)"
      >
        <Flex justify="space-between" align="center" maxW="container.sm" mx="auto">
          <Heading size="md">{title ?? "agent-harness"}</Heading>
          <Box>{right}</Box>
        </Flex>
      </Box>
      <Box as="main" flex="1" pb="calc(72px + env(safe-area-inset-bottom))">
        <Container maxW="container.sm" px={4} py={4}>
          {children}
        </Container>
      </Box>
      <Box
        as="nav"
        position="fixed"
        bottom={0}
        left={0}
        right={0}
        bg="bg"
        borderTopWidth="1px"
        pb="env(safe-area-inset-bottom)"
        zIndex={2}
      >
        <HStack justify="space-around" py={2} maxW="container.sm" mx="auto">
          {TABS.map((t) => {
            const active = loc.pathname.startsWith(t.to);
            return (
              <Link
                as={RouterLink}
                key={t.to}
                // @ts-expect-error react-router Link prop
                to={t.to}
                fontWeight={active ? "bold" : "normal"}
                color={active ? "fg" : "fg.muted"}
                textDecoration="none"
                fontSize="sm"
                px={4}
                py={2}
              >
                <Text>{t.label}</Text>
              </Link>
            );
          })}
        </HStack>
      </Box>
    </Flex>
  );
}
