const { MongoClient } = require('mongodb');

async function setup() {
  const client = new MongoClient('mongodb://stock:681123@192.168.1.2:27017/admin');
  await client.connect();
  const db = client.db('knowledge_graph');

  await db.createCollection('triples', {
    validator: {
      $jsonSchema: {
        bsonType: 'object',
        required: ['subject', 'relation', 'object'],
        properties: {
          subject: { bsonType: 'string' },
          relation: { bsonType: 'string' },
          object: { bsonType: ['string', 'double', 'int'] },
          source: { bsonType: 'string' },
          confidence: { bsonType: 'double' },
          created_at: { bsonType: 'date' }
        }
      }
    }
  });

  await db.createCollection('inferred_triples', {
    validator: {
      $jsonSchema: {
        bsonType: 'object',
        required: ['subject', 'relation', 'object'],
        properties: {
          subject: { bsonType: 'string' },
          relation: { bsonType: 'string' },
          object: { bsonType: ['string', 'double', 'int'] },
          source: { bsonType: ['array', 'string'] },
          confidence: { bsonType: 'double' },
          created_at: { bsonType: 'date' }
        }
      }
    }
  });

  await db.createCollection('documents', {
    validator: {
      $jsonSchema: {
        bsonType: 'object',
        required: ['doc_id', 'path'],
        properties: {
          doc_id: { bsonType: 'string' },
          path: { bsonType: 'string' },
          title: { bsonType: 'string' },
          tags: { bsonType: 'array' }
        }
      }
    }
  });

  await db.collection('triples').createIndex({ subject: 1 });
  await db.collection('triples').createIndex({ object: 1 });
  await db.collection('triples').createIndex({ relation: 1 });
  await db.collection('inferred_triples').createIndex({ subject: 1 });
  await db.collection('inferred_triples').createIndex({ object: 1 });
  await db.collection('documents').createIndex({ doc_id: 1 }, { unique: true });

  const cols = await db.listCollections().toArray();
  console.log('Collections:', cols.map(c => c.name).join(', '));

  for (const name of ['triples', 'inferred_triples', 'documents']) {
    const idx = await db.collection(name).indexes();
    console.log(`\n${name}:`);
    idx.forEach(i => console.log(`  ${i.name}: ${JSON.stringify(i.key)}${i.unique ? ' (UNIQUE)' : ''}`));
  }

  await client.close();
  console.log('\nDone!');
}

setup().catch(e => { console.error(e.message); process.exit(1); });
