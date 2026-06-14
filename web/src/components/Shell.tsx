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
  subtitle?: ReactNode;
  right?: ReactNode;
  back?: string | true;
  children: ReactNode;
  /** Reserve space at the bottom for a sticky composer. Mobile sits ABOVE
   *  the tab bar; desktop sits at the page bottom. */
  composerHeight?: number;
}

const NAV = [
  { to: "/", label: "Projects", icon: <LuFolderGit2 /> },
  { to: "/jobs", label: "Jobs", icon: <LuListTodo /> },
  { to: "/schedules", label: "Schedules", icon: <LuClock /> },
] as const;

const MOBILE_TAB_HEIGHT = 56; // matches the tab bar px height below
// Cap content at a comfortable reading width on desktop (~85 characters).
// Mobile is full-width minus container padding.
const CONTENT_MAX_W = { base: "100%", md: "85ch", xl: "6xl" } as const;

function isCurrent(pathname: string, to: string): boolean {
  if (to === "/")
    return (
      pathname === "/" ||
      pathname.startsWith("/?") ||
      pathname.startsWith("/projects")
    );
  return (
    pathname === to ||
    pathname.startsWith(`${to}/`) ||
    pathname.startsWith(`${to}?`)
  );
}

export function Shell({
  title,
  subtitle,
  right,
  back,
  children,
  composerHeight = 0,
}: ShellProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const [settingsOpen, setSettingsOpen] = useState(false);

  const backButton = back ? (
    <IconButton
      aria-label="Back"
      variant="ghost"
      size="sm"
      onClick={() => (back === true ? navigate(-1) : navigate(back))}
    >
      <LuChevronLeft />
    </IconButton>
  ) : null;

  // ------------------------------- DESKTOP -------------------------------- //
  // Top chrome bar: brand + nav + settings. Sub-header: title/subtitle/actions.

  const desktopChrome = (
    <Box
      as="header"
      hideBelow="md"
      position="sticky"
      top={0}
      zIndex={20}
      bg="bg.subtle"
      borderBottomWidth="1px"
      borderColor="border.subtle"
    >
      <Flex align="center" gap={6} px={{ base: 4, md: 6 }} py={2}>
        <Text fontWeight="semibold" fontSize="sm" color="fg.muted">
          agent-harness
        </Text>
        <HStack gap={1} flex="1">
          {NAV.map((n) => {
            const current = isCurrent(location.pathname, n.to);
            return (
              <Flex
                as={RouterLink}
                key={n.to}
                // @ts-expect-error chakra/router prop interop
                to={n.to}
                align="center"
                px={3}
                py={1.5}
                rounded="md"
                fontSize="sm"
                fontWeight={current ? "medium" : "normal"}
                color={current ? "fg" : "fg.muted"}
                bg={current ? "bg" : "transparent"}
                _hover={{ bg: "bg", color: "fg" }}
              >
                {n.label}
              </Flex>
            );
          })}
        </HStack>
        <IconButton
          aria-label="Settings"
          variant="ghost"
          size="sm"
          onClick={() => setSettingsOpen(true)}
        >
          <LuSettings />
        </IconButton>
      </Flex>
    </Box>
  );

  const desktopSubHeader = title ? (
    <Box
      hideBelow="md"
      position="sticky"
      top="52px" // height of desktopChrome row
      zIndex={15}
      bg="bg.subtle"
      borderBottomWidth="1px"
      borderColor="border.subtle"
    >
      <Container maxW={CONTENT_MAX_W} px={{ base: 4, md: 6 }} py={2}>
        <Flex align="flex-start" gap={3}>
          {backButton}
          <Stack gap={0.5} flex="1" minW={0}>
            <Heading size="md" lineHeight="short" truncate>
              {title}
            </Heading>
            {subtitle && (
              <Text fontSize="xs" color="fg.muted" truncate>
                {subtitle}
              </Text>
            )}
          </Stack>
          {right && (
            <HStack gap={2} flexShrink={0} alignSelf="center">
              {right}
            </HStack>
          )}
        </Flex>
      </Container>
    </Box>
  ) : null;

  // ------------------------------- MOBILE -------------------------------- //
  // Compact top: back + title/subtitle + actions. Bottom: tab bar.

  const mobileHeader = (
    <Box
      as="header"
      hideFrom="md"
      position="sticky"
      top={0}
      zIndex={20}
      bg="bg.subtle"
      borderBottomWidth="1px"
      borderColor="border.subtle"
      pt="max(env(safe-area-inset-top), 8px)"
    >
      <Flex align="flex-start" gap={2} px={3} py={2}>
        {backButton}
        <Stack gap={0} flex="1" minW={0} pt={1}>
          <Heading size="sm" lineHeight="short" truncate>
            {title ?? "agent-harness"}
          </Heading>
          {subtitle && (
            <Text fontSize="2xs" color="fg.muted" truncate>
              {subtitle}
            </Text>
          )}
        </Stack>
        {right && (
          <HStack gap={1.5} flexShrink={0} pt={0.5}>
            {right}
          </HStack>
        )}
      </Flex>
    </Box>
  );

  const mobileTabs = (
    <Box
      as="nav"
      hideFrom="md"
      position="fixed"
      left={0}
      right={0}
      bottom={0}
      zIndex={25}
      bg="bg.subtle"
      borderTopWidth="1px"
      borderColor="border.subtle"
      pb="env(safe-area-inset-bottom)"
    >
      <HStack gap={0} justify="space-around" h={`${MOBILE_TAB_HEIGHT}px`}>
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
              justify="center"
              gap={0.5}
              flex="1"
              h="full"
              fontSize="2xs"
              fontWeight="medium"
              color={current ? "fg" : "fg.muted"}
              _active={{ bg: "bg" }}
            >
              <Box fontSize="lg" lineHeight="0">
                {n.icon}
              </Box>
              {n.label}
            </Flex>
          );
        })}
        <Flex
          direction="column"
          align="center"
          justify="center"
          gap={0.5}
          flex="1"
          h="full"
          fontSize="2xs"
          fontWeight="medium"
          color="fg.muted"
          cursor="pointer"
          onClick={() => setSettingsOpen(true)}
          _active={{ bg: "bg" }}
        >
          <Box fontSize="lg" lineHeight="0">
            <LuSettings />
          </Box>
          Settings
        </Flex>
      </HStack>
    </Box>
  );

  // The body needs bottom padding to clear (composer height + mobile tabs).
  // Desktop pads only for composer; mobile pads for composer + tab bar.
  const bottomPadMobile = `calc(${composerHeight + MOBILE_TAB_HEIGHT}px + env(safe-area-inset-bottom))`;
  const bottomPadDesktop = `${composerHeight}px`;

  return (
    <Flex direction="column" minH="100dvh" bg="bg.subtle">
      {desktopChrome}
      {desktopSubHeader}
      {mobileHeader}
      <Box
        as="main"
        flex="1"
        overflowX="hidden"
        w="100%"
        pb={{ base: bottomPadMobile, md: bottomPadDesktop }}
      >
        <Container
          maxW={CONTENT_MAX_W}
          px={{ base: 3, md: 6 }}
          pt={{ base: 3, md: 3 }}
          pb={{ base: 3, md: 4 }}
          w="100%"
        >
          {children}
        </Container>
      </Box>
      {mobileTabs}
      <SettingsDrawer
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
      />
    </Flex>
  );
}

export { MOBILE_TAB_HEIGHT };
