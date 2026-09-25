// Copyright (c) Microsoft Corporation.
// SPDX-License-Identifier: MIT

import React from 'react';
import Head from '@docusaurus/Head';
import Layout from '@theme/Layout';

export default function ScreenReaderCalibration(): React.ReactElement {
  return (
    <Layout>
      <Head>
        <title>Screen reader calibration</title>
        <meta
          name="description"
          content="Positive controls for validating the documentation screen-reader test environment"
        />
      </Head>
      <main className="container margin-vert--lg">
        <h1 data-a11y-positive-control="headings">Screen reader calibration</h1>
        <p>
          Use these controls to verify that the accessibility test environment reports
          headings, labels, and state changes before evaluating documentation journeys.
        </p>
        <section aria-labelledby="interaction-control-heading">
          <h2 id="interaction-control-heading" data-a11y-positive-control="headings">
            Interaction control
          </h2>
          <label>
            <input type="checkbox" data-a11y-positive-control="checkbox" /> Accept terms
          </label>
        </section>
      </main>
    </Layout>
  );
}
