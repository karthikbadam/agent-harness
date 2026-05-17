import { ReactNode, useState } from "react";
import { Link as RouterLink, useLocation, useNavigate } from "react-router-dom";
import {
  Box,
  Container,
  Flex,
  HStack,
  Heading,
  IconButton,
  Stack,
  Text,
} from "@chakra-ui/react";
import {
  LuChevronLeft,
  LuFolderGit2,
  LuListTodo,
  LuClock,
  LuSettings,
} from "react-icons/lu";

import { SettingsDrawer } from "./SettingsDrawer";

interface ShellProps {
  title?: ReactNode;
  right?: ReactNode;
  back?: string | true;
  children: ReactNode;
  fullBleed?: boolean;
}

const NAV = [
  { to: "/", label: "Projects", icon: <LuFolderGit2 /> },
  { to: "/jobs", label: "Jobs", icon: <LuListTodo /> },
  { to: "/schedules", label: "Schedules", icon: <LuClock /> },
] as const;

function isCurrent(pathname: string, to: string): boolean {
  if (to === "/") return pathname === "/" || pathname.startsWith("/?");
  return pathname === to || pathname.startsWith(`${to}/`) || pathname.startsWith(`${to}?`);
}

export function Shell({ title, right, back, children, fullBleed = false }: ShellProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const [settingsOpen, setSettingsOpen] = useState(false);

  const desktopNav = (
    <Stack
      as="nav"
      gap={1}
      position="sticky"
      top={0}
      px={3}
      py={6}
      borderRightWidth="1px"
      borderColor="border.subtle"
      bg="bg.subtle"
      display={{ base: "none", md: "flex" }}
      h="100dvh"
      w="56"
      flexShrink={0}
    >
      <Heading size="sm" px={2} mb={4} color="fg.muted">
        agent-harness
      </Heading>
      {NAV.map((n) => {
        const current = isCurrent(location.pathname, n.to);
        return (
          <Flex
            as={RouterLink}
            key={n.to}
            // @ts-expect-error chakra/router prop interop
            to={n.to}
            align="center"
            gap={2}
            px={3}
            py={2}
            rounded="md"
            fontSize="sm"
            color={current ? "fg" : "fg.muted"}
            bg={current ? "bg" : "transparent"}
            fontWeight={current ? "medium" : "normal"}
            _hover={{ bg: "bg", color: "fg" }}
          >
            <Box fontSize="md" lineHeight="0" color={current ? "fg" : "fg.muted"}>
              {n.icon}
            </Box>
            {n.label}
          </Flex>
        );
      })}
      <Box flex="1" />
      <IconButton
        aria-label="Settings"
        variant="ghost"
        size="sm"
        onClick={() => setSettingsOpen(true)}
        justifyContent="flex-start"
        px={3}
      >
        <LuSettings />
        <Text ml={2} fontSize="sm" fontWeight="normal">
          Settings
        </Text>
      </IconButton>
    </Stack>
  );

  const mobileNav = (
    <Box
      as="nav"
      borderBottomWidth="1px"
      borderColor="border.subtle"
      bg="bg"
      px={2}
      py={1}
      display={{ base: "block", md: "none" }}
    >
      <HStack gap={0} justify="center">
        {NAV.map((n) => {
          const current = isCurrent(location.pathname, n.to);
          return (
            <Flex
              as={RouterLink}
              key={n.to}
              // @ts-expect-error chakra/router prop interop
              to={n.to}
              direction="column"
              align="center"
              gap={0.5}
              px={3}
              py={1.5}
              flex="1"
              maxW="32"
              fontSize="2xs"
              fontWeight="medium"
              color={current ? "fg" : "fg.muted"}
              borderBottomWidth="2px"
              borderColor={current ? "fg.emphasized" : "transparent"}
              transition="color 0.15s"
            >
              <Box fontSize="lg" lineHeight="0">
                {n.icon}
              </Box>
              {n.label}
            </Flex>
          );
        })}
      </HStack>
    </Box>
  );

  return (
    <Flex minH="100dvh" bg="bg" direction={{ base: "column", md: "row" }}>
      {desktopNav}
      <Flex direction="column" flex="1" minW={0}>
        <Box
          as="header"
          position="sticky"
          top={0}
          zIndex={10}
          bg="bg"
          borderBottomWidth="1px"
          borderColor="border.subtle"
          px={{ base: 3, md: 6 }}
          py={3}
          pt={{ base: "max(env(safe-area-inset-top), 12px)", md: 4 }}
        >
          <Flex justify="space-between" align="center" gap={2}>
            <HStack gap={2} flex="1" minW={0}>
              {back && (
                <IconButton
                  aria-label="Back"
                  variant="ghost"
                  size="sm"
                  onClick={() => (back === true ? navigate(-1) : navigate(back))}
                >
                  <LuChevronLeft />
                </IconButton>
              )}
              <Heading size="md" truncate>
                {title ?? "agent-harness"}
              </Heading>
            </HStack>
            <HStack gap={1}>
              {right}
              <IconButton
                aria-label="Settings"
                variant="ghost"
                size="sm"
                onClick={() => setSettingsOpen(true)}
                display={{ base: "inline-flex", md: "none" }}
              >
                <LuSettings />
              </IconButton>
            </HStack>
          </Flex>
        </Box>
        {mobileNav}
        <Box as="main" flex="1" overflowX="hidden" w="100%">
          {fullBleed ? (
            children
          ) : (
            <Container
              maxW={{ base: "container.sm", md: "container.lg", lg: "container.xl" }}
              px={{ base: 3, md: 6 }}
              py={{ base: 4, md: 6 }}
              w="100%"
            >
              {children}
            </Container>
          )}
        </Box>
      </Flex>
      <SettingsDrawer open={settingsOpen} onClose={() => setSettingsOpen(false)} />
    </Flex>
  );
}
