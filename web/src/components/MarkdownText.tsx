import { ComponentProps } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Box, Code, Heading, Link, List, Table, Text } from "@chakra-ui/react";

const components: ComponentProps<typeof ReactMarkdown>["components"] = {
  h1: ({ children }) => (
    <Heading as="h1" size="lg" mt={4} mb={2}>
      {children}
    </Heading>
  ),
  h2: ({ children }) => (
    <Heading as="h2" size="md" mt={4} mb={2}>
      {children}
    </Heading>
  ),
  h3: ({ children }) => (
    <Heading as="h3" size="sm" mt={3} mb={1}>
      {children}
    </Heading>
  ),
  h4: ({ children }) => (
    <Heading as="h4" size="xs" mt={2} mb={1}>
      {children}
    </Heading>
  ),
  p: ({ children }) => (
    <Text mb={2} lineHeight="1.6">
      {children}
    </Text>
  ),
  a: ({ href, children }) => (
    <Link
      href={href}
      color="blue.fg"
      textDecoration="underline"
      target="_blank"
      rel="noopener noreferrer"
    >
      {children}
    </Link>
  ),
  ul: ({ children }) => (
    <List.Root mb={2} pl={4}>
      {children}
    </List.Root>
  ),
  ol: ({ children }) => (
    <List.Root as="ol" mb={2} pl={4}>
      {children}
    </List.Root>
  ),
  li: ({ children }) => <List.Item lineHeight="1.6">{children}</List.Item>,
  code: ({ className, children }) => {
    const isBlock = /language-/.test(className ?? "");
    if (isBlock) {
      return (
        <Code
          as="pre"
          display="block"
          whiteSpace="pre"
          fontSize="xs"
          p={3}
          my={2}
          borderRadius="md"
          overflowX="auto"
          maxW="100%"
        >
          {children}
        </Code>
      );
    }
    return (
      <Code
        fontSize="0.9em"
        px={1}
        py={0.5}
        borderRadius="sm"
        wordBreak="break-word"
      >
        {children}
      </Code>
    );
  },
  pre: ({ children }) => (
    <Box my={2} maxW="100%" overflowX="auto">
      {children}
    </Box>
  ),
  table: ({ children }) => (
    <Box overflowX="auto" my={3} borderWidth="1px" borderRadius="md">
      <Table.Root size="sm" variant="line">
        {children}
      </Table.Root>
    </Box>
  ),
  thead: ({ children }) => <Table.Header>{children}</Table.Header>,
  tbody: ({ children }) => <Table.Body>{children}</Table.Body>,
  tr: ({ children }) => <Table.Row>{children}</Table.Row>,
  th: ({ children }) => (
    <Table.ColumnHeader
      fontWeight="semibold"
      minW="7rem"
      verticalAlign="top"
      whiteSpace="normal"
    >
      {children}
    </Table.ColumnHeader>
  ),
  td: ({ children }) => (
    <Table.Cell
      minW="7rem"
      maxW="18rem"
      verticalAlign="top"
      whiteSpace="normal"
      css={{ overflowWrap: "anywhere" }}
    >
      {children}
    </Table.Cell>
  ),
  blockquote: ({ children }) => (
    <Box
      borderLeftWidth="3px"
      borderLeftColor="border.emphasized"
      pl={3}
      py={1}
      my={2}
      color="fg.muted"
      fontStyle="italic"
    >
      {children}
    </Box>
  ),
  hr: () => <Box as="hr" my={3} borderTopWidth="1px" />,
  strong: ({ children }) => (
    <Text as="strong" fontWeight="semibold">
      {children}
    </Text>
  ),
  em: ({ children }) => (
    <Text as="em" fontStyle="italic">
      {children}
    </Text>
  ),
};

export function MarkdownText({ source }: { source: string }) {
  return (
    <Box
      fontSize="sm"
      maxW="100%"
      minW={0}
      overflowWrap="anywhere"
      wordBreak="break-word"
    >
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {source}
      </ReactMarkdown>
    </Box>
  );
}
