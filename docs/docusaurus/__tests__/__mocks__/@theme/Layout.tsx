import React from 'react';

export default function Layout({
  children,
}: {
  children?: React.ReactNode;
}): React.ReactElement {
  return <div>{children}</div>;
}
