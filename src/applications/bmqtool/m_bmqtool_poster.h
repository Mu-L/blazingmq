// Copyright 2024 Bloomberg Finance L.P.
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

// m_bmqtool_poster.h                                                 -*-C++-*-
#ifndef INCLUDED_M_BMQTOOL_POSTER
#define INCLUDED_M_BMQTOOL_POSTER

//@PURPOSE: Helper classes for posting series of messages.
//
//@CLASSES:
//
//  m_bmqtool::PostingContext: A class to hold a context of a single series
//  of messages to be posted.
//  m_bmqtool::Poster: A factory-semantic class to hold everything needed for
//  posting and ease creating posting contexts: buffers,
//  a logger, an allocator etc.
//
//@DESCRIPTION: 'm_bmqtool_poster' contains classes that abstract the mechanism
// of posting series of messages.

// BMQTOOL
#include <m_bmqtool_filelogger.h>
#include <m_bmqtool_parameters.h>

// BMQ
#include <bmqa_messageproperties.h>
#include <bmqa_session.h>
#include <bmqst_statcontext.h>

// BDE
#include <bdlbb_pooledblobbufferfactory.h>
#include <bsl_memory.h>
#include <bsl_string.h>

namespace BloombergLP {
namespace m_bmqtool {

// ====================
// class PostingContext
// ====================

/// A class to hold a context of a single series of messages to be posted.
class PostingContext {
  private:
    // DATA
    bslma::Allocator* d_allocator_p;
    // Held, not owned.

    bdlbb::PooledBlobBufferFactory* d_timeBufferFactory_p;
    // Small buffer factory for the first
    // blob of the published message, used to
    // hold the timestamp information.

    /// Parameters to use, the object under this reference is owned by
    /// the Application that uses this Interactive object
    const Parameters& d_parameters;

    bmqa::Session* d_session_p;
    // A session to post messages.
    // Held, not owned.

    FileLogger* d_fileLogger;
    // Where to log posted messages.
    // Held, not owned.

    bmqst::StatContext* d_statContext_p;
    // StatContext for msg/event stats.
    // Held, not owned

    int d_remainingEvents;
    // How many events are still left for posting.

    int d_numMessagesPosted;
    // How many events have already been posted.

    bdlbb::Blob d_blob;
    // Blob to post

    bmqa::QueueId d_queueId;
    // Queue ID for posting

    bmqa::MessageProperties d_properties;
    // Properties that will be added to a posted message

    bsls::Types::Uint64 d_autoIncrementedValue;
    // A value that will be auto-incremented and added to
    // the message properties.

  public:
    // CREATORS

    PostingContext(bmqa::Session*                  session,
                   const Parameters&               parameters,
                   const bmqa::QueueId&            queueId,
                   FileLogger*                     fileLogger,
                   bmqst::StatContext*             statContext,
                   bdlbb::PooledBlobBufferFactory* bufferFactory,
                   bdlbb::PooledBlobBufferFactory* timeBufferFactory,
                   bslma::Allocator*               allocator);

    // MANIPULATORS

    /// Post next message. The behavior is undefined unless pendingPost()
    /// is true.
    void postNext();

    // ACCESSORS

    /// Return true is there is at least one message which should be posted.
    bool pendingPost() const;

  private:
    // NOT IMPLEMENTED
    PostingContext(const PostingContext&) BSLS_KEYWORD_DELETED;
    PostingContext& operator=(const PostingContext&) BSLS_KEYWORD_DELETED;
};

// ============
// class Poster
// ============

// A factory-semantic class to hold everything needed for posting
// and ease creating posting contexts: buffers, a logger, an allocator etc.
class Poster {
    // DATA
    bslma::Allocator* d_allocator_p;
    // Held, not owned

    bdlbb::PooledBlobBufferFactory d_bufferFactory;
    // Buffer factory for the payload of the
    // published message

    bdlbb::PooledBlobBufferFactory d_timeBufferFactory;
    // Small buffer factory for the first
    // blob of the published message, to
    // hold the timestamp information

    bmqst::StatContext* d_statContext;
    // StatContext for msg/event stats.
    // Held, not owned

    FileLogger* d_fileLogger;
    // Logger to use in case events logging
    // to file has been enabled.
    // Held, not owned

  public:
    // CREATORS
    Poster(FileLogger*         fileLogger,
           bmqst::StatContext* statContext,
           bslma::Allocator*   allocator);

    // MANIPULATORS
    bsl::shared_ptr<PostingContext>
    createPostingContext(bmqa::Session*       session,
                         const Parameters&    parameters,
                         const bmqa::QueueId& queueId);

  private:
    // NOT IMPLEMENTED
    Poster(const Poster&) BSLS_KEYWORD_DELETED;
    Poster& operator=(const Poster&) BSLS_KEYWORD_DELETED;
};

}  // close package namespace
}  // close enterprise namespace

#endif  // INCLUDED_M_BMQTOOL_POSTER
