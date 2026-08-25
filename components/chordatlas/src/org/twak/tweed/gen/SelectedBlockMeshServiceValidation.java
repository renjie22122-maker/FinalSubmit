package org.twak.tweed.gen;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import javax.vecmath.Point3d;

import org.twak.utils.collections.Loop;
import org.twak.utils.collections.LoopL;

/** Lightweight validation executable for projects which do not carry JUnit. */
public final class SelectedBlockMeshServiceValidation {

	private SelectedBlockMeshServiceValidation() {}

	public static void main( String[] args ) throws Exception {
		LoopL<Point3d> first = rectangle( false, 0 );
		LoopL<Point3d> rotatedAndReversed = rectangle( true, 2 );
		String firstId = SelectedBlockMeshService.stableSelectionId( first );
		String secondId = SelectedBlockMeshService.stableSelectionId( rotatedAndReversed );
		if ( !firstId.equals( secondId ) )
			throw new AssertionError( "selection id must ignore loop start and winding" );

		LoopL<Point3d> copy = SelectedBlockMeshService.deepCopy( first );
		first.get( 0 ).start.get().x = 99;
		if ( copy.get( 0 ).start.get().x == 99 )
			throw new AssertionError( "selected footprint was not deeply copied" );

		LoopL<Point3d> duplicated = rectangle( false, 0 );
		duplicated.add( rectangle( true, 2 ).get( 0 ) );
		if ( !SelectedBlockMeshService.stableSelectionId( duplicated ).equals( secondId ) )
			throw new AssertionError( "duplicate geometry must not change the selection id" );
		LoopL<Point3d> deduplicated = SelectedBlockMeshService.deepCopy( duplicated );
		if ( deduplicated.size() != 1 )
			throw new AssertionError( "duplicate footprint geometry must be removed before request creation" );

		validateReadyCacheIdentity();
		validateBigImagePublication();
		validateRoofBackfillCommand();

		if ( args.length == 1 )
			SelectedBlockMeshService.validateBlockDirectory( new File( args[ 0 ] ) );
		else if ( args.length == 2 && "big-image".equals( args[ 1 ] ) )
			SelectedBlockMeshService.validateBlockDirectory( new File( args[ 0 ] ),
					new File( args[ 0 ] ).getName(),
					SelectedBlockMeshService.BIG_IMAGE_PIPELINE_CONTRACT_VERSION, false );
		System.out.println( "SelectedBlockMeshService validation passed" );
	}

	private static void validateBigImagePublication() throws Exception {
		Path directory = Files.createTempDirectory( "selected-big-image-validation-" );
		writeRootObjs( directory );
		String ready = readyEntry( "footprint-big", "buildings/footprint-big" );
		writeBuildingDirectory( directory.resolve( "buildings" ).resolve( "footprint-big" ), ready );
		writePublication( directory, ready,
				"{\"requested\":1,\"ready\":1,\"coarse_ready\":1,\"rejected\":0,\"empty\":0,\"failed\":0}",
				SelectedBlockMeshService.BUILDING_PUBLICATION_VERSION,
				SelectedBlockMeshService.BIG_IMAGE_PIPELINE_CONTRACT_VERSION, false );
		SelectedBlockMeshService.validateBlockDirectory( directory.toFile(), "cache-id",
				SelectedBlockMeshService.BIG_IMAGE_PIPELINE_CONTRACT_VERSION, false );
		boolean rejectedByLegacyContract = false;
		try {
			SelectedBlockMeshService.validateBlockDirectory( directory.toFile(), "cache-id" );
		} catch ( IOException expected ) {
			rejectedByLegacyContract = true;
		}
		if ( !rejectedByLegacyContract )
			throw new AssertionError( "big-image cache must not collide with the satellite-tile contract" );
		writePublication( directory, ready,
				"{\"requested\":1,\"ready\":1,\"coarse_ready\":1,\"rejected\":0,\"empty\":0,\"failed\":0}",
				SelectedBlockMeshService.BUILDING_PUBLICATION_VERSION,
				"big-image-app192-v1", false );
		boolean inwardV1Rejected = false;
		try {
			SelectedBlockMeshService.validateBlockDirectory( directory.toFile(), "cache-id",
					SelectedBlockMeshService.BIG_IMAGE_PIPELINE_CONTRACT_VERSION, false );
		} catch ( IOException expected ) {
			inwardV1Rejected = true;
		}
		if ( !inwardV1Rejected )
			throw new AssertionError( "inward-wound big-image v1 cache must be rebuilt as v2" );
		if ( !"big-image-".equals( SelectedBlockMeshService.ModelSource.BIG_IMAGE.selectionPrefix ) )
			throw new AssertionError( "big-image selection IDs require a separate cache prefix" );
	}

	private static void validateRoofBackfillCommand() {
		if ( SelectedBlockMeshService.ROOF_BACKFILL_TIMEOUT_SECONDS != 120 )
			throw new AssertionError( "roof backfill must have a bounded 120-second wait" );
		List<String> command = SelectedBlockMeshService.roofBackfillCommand(
				new File( "conda.exe" ), "sat3dgen", new File( "bridge/run.py" ),
				new File( "generated_blocks/cache-id" ) );
		String joined = String.join( " ", command );
		if ( !command.contains( "backfill-roof-references" ) || !command.contains( "--publication" ) )
			throw new AssertionError( "READY cache must invoke the appearance-only roof backfill" );
		if ( command.contains( "build-selection" ) || command.contains( "--execute" ) )
			throw new AssertionError( "roof backfill must not invoke download/GPU/mesh generation" );
		if ( joined.toLowerCase().contains( "api_key" ) || joined.toLowerCase().contains( "apikey" ) )
			throw new AssertionError( "roof backfill command must not contain an API key" );
	}

	private static void validateReadyCacheIdentity() throws Exception {
		Path directory = Files.createTempDirectory( "selected-mesh-validation-" );
		writeRootObjs( directory );
		String ready = readyEntry( "footprint-ready", "buildings/footprint-ready" );
		String rejected = rejectedEntry( "footprint-rejected" );
		writeBuildingDirectory( directory.resolve( "buildings" ).resolve( "footprint-ready" ), ready );
		writePublication( directory, ready + "," + rejected,
				"{\"requested\":2,\"ready\":1,\"coarse_ready\":1,\"rejected\":1,\"empty\":0,\"failed\":0}" );
		SelectedBlockMeshService.validateBlockDirectory( directory.toFile(), "cache-id" );

		boolean mismatchRejected = false;
		try {
			SelectedBlockMeshService.validateBlockDirectory( directory.toFile(), "another-id" );
		} catch ( IOException expected ) {
			mismatchRejected = true;
		}
		if ( !mismatchRejected )
			throw new AssertionError( "READY cache with the wrong identity must be rejected" );

		Files.write( directory.resolve( "result.json" ),
				("{\"status\":\"READY\",\"selection_id\":\"cache-id\",\"stable_id\":\"cache-id\","
						+ "\"pipeline_contract_version\":\"osm-prealign-v1\",\"osm_prealign\":true}")
						.getBytes( StandardCharsets.UTF_8 ) );
		boolean legacyCacheRejected = false;
		try {
			SelectedBlockMeshService.validateBlockDirectory( directory.toFile(), "cache-id" );
		} catch ( IOException expected ) {
			legacyCacheRejected = true;
		}
		if ( !legacyCacheRejected )
			throw new AssertionError( "READY cache without per-building publication must be rejected" );

		validateV1PublicationRejected();
		validateDuplicateBuildingRejected();
		validateEscapingBuildingRejected();
	}

	private static void validateV1PublicationRejected() throws Exception {
		Path directory = Files.createTempDirectory( "selected-mesh-v1-publication-" );
		writeRootObjs( directory );
		String entry = readyEntry( "footprint-v1", "buildings/footprint-v1" );
		writeBuildingDirectory( directory.resolve( "buildings" ).resolve( "footprint-v1" ), entry );
		writePublication( directory, entry,
				"{\"requested\":1,\"ready\":1,\"coarse_ready\":1,\"rejected\":0,\"empty\":0,\"failed\":0}",
				"per-footprint-v1" );
		boolean rejected = false;
		try {
			SelectedBlockMeshService.validateBlockDirectory( directory.toFile(), "cache-id" );
		} catch ( IOException expected ) {
			rejected = true;
		}
		if ( !rejected )
			throw new AssertionError( "complete per-footprint-v1 READY cache must be rebuilt" );
	}

	private static void validateDuplicateBuildingRejected() throws Exception {
		Path directory = Files.createTempDirectory( "selected-mesh-duplicate-" );
		writeRootObjs( directory );
		String entry = readyEntry( "footprint-duplicate", "buildings/footprint-duplicate" );
		writePublication( directory, entry + "," + entry,
				"{\"requested\":2,\"ready\":2,\"coarse_ready\":2,\"rejected\":0,\"empty\":0,\"failed\":0}" );
		boolean rejected = false;
		try {
			SelectedBlockMeshService.validateBlockDirectory( directory.toFile(), "cache-id" );
		} catch ( IOException expected ) {
			rejected = true;
		}
		if ( !rejected )
			throw new AssertionError( "duplicate per-building IDs must be rejected" );
	}

	private static void validateEscapingBuildingRejected() throws Exception {
		Path directory = Files.createTempDirectory( "selected-mesh-traversal-" );
		writeRootObjs( directory );
		String entry = readyEntry( "footprint-escape", "../outside" );
		writePublication( directory, entry,
				"{\"requested\":1,\"ready\":1,\"coarse_ready\":1,\"rejected\":0,\"empty\":0,\"failed\":0}" );
		boolean rejected = false;
		try {
			SelectedBlockMeshService.validateBlockDirectory( directory.toFile(), "cache-id" );
		} catch ( IOException expected ) {
			rejected = true;
		}
		if ( !rejected )
			throw new AssertionError( "a building directory outside buildings/ must be rejected" );
	}

	private static void writeRootObjs( Path directory ) throws IOException {
		for ( String name : new String[] { "cropped.obj", "gis.obj", "gis_footprints.obj" } )
			writeObj( directory.resolve( name ) );
	}

	private static void writeBuildingDirectory( Path directory, String metadata ) throws IOException {
		Files.createDirectories( directory );
		for ( String name : new String[] { "cropped.obj", "gis.obj", "gis_footprints.obj" } )
			writeObj( directory.resolve( name ) );
		Files.write( directory.resolve( "building.json" ), metadata.getBytes( StandardCharsets.UTF_8 ) );
	}

	private static void writeObj( Path path ) throws IOException {
		Files.createDirectories( path.getParent() );
		Files.write( path, "v 0 0 0\nv 1 0 0\nv 0 0 1\nf 1 2 3\n".getBytes( StandardCharsets.UTF_8 ) );
	}

	private static String readyEntry( String footprintId, String relativeDirectory ) {
		return "{\"id\":\"" + footprintId + "\",\"component_id\":\"" + footprintId
				+ "\",\"footprint_id\":\"" + footprintId + "\",\"footprint_ids\":[\"" + footprintId
				+ "\"],\"source_feature_id\":\"way/42\",\"osm_type\":\"way\",\"osm_id\":\"42\","
				+ "\"status\":\"COARSE_READY\",\"publishable\":true,\"relative_dir\":\""
				+ relativeDirectory + "\",\"outputs\":{\"cropped_obj\":\"" + relativeDirectory
				+ "/cropped.obj\",\"gis_obj\":\"" + relativeDirectory
				+ "/gis.obj\",\"gis_footprints_obj\":\"" + relativeDirectory
				+ "/gis_footprints.obj\"},\"metrics\":{\"vertex_count\":3,\"face_count\":1}}";
	}

	private static String rejectedEntry( String footprintId ) {
		return "{\"id\":\"" + footprintId + "\",\"component_id\":\"" + footprintId
				+ "\",\"footprint_id\":\"" + footprintId + "\",\"footprint_ids\":[\"" + footprintId
				+ "\"],\"source_feature_id\":null,\"osm_type\":null,\"osm_id\":null,"
				+ "\"status\":\"REJECTED\",\"publishable\":false,\"relative_dir\":\"buildings/"
				+ footprintId + "\",\"outputs\":null,\"metrics\":{\"vertex_count\":0,\"face_count\":0}}";
	}

	private static void writePublication( Path directory, String entries, String summary ) throws IOException {
		writePublication( directory, entries, summary, SelectedBlockMeshService.BUILDING_PUBLICATION_VERSION );
	}

	private static void writePublication( Path directory, String entries, String summary,
			String publicationVersion ) throws IOException {
		writePublication( directory, entries, summary, publicationVersion,
				SelectedBlockMeshService.PIPELINE_CONTRACT_VERSION, true );
	}

	private static void writePublication( Path directory, String entries, String summary,
			String publicationVersion, String pipelineContract, boolean osmPrealign ) throws IOException {
		Files.createDirectories( directory.resolve( "buildings" ) );
		String index = "{\"schema_version\":1,\"kind\":\"myProject.selection.buildings\","
				+ "\"building_publication_version\":\"" + publicationVersion + "\","
				+ "\"pipeline_contract_version\":\"" + pipelineContract + "\"," 
				+ "\"selection_id\":\"cache-id\",\"stable_id\":\"cache-id\",\"status\":\"READY\","
				+ "\"summary\":" + summary + ",\"buildings\":[" + entries + "]}";
		String result = "{\"status\":\"READY\",\"selection_id\":\"cache-id\",\"stable_id\":\"cache-id\","
				+ "\"pipeline_contract_version\":\"" + pipelineContract + "\",\"building_publication_version\":\""
				+ publicationVersion + "\","
				+ "\"osm_prealign\":" + osmPrealign + ",\"buildings_summary\":" + summary + ",\"buildings\":[" + entries + "],"
				+ "\"outputs\":{\"buildings_index\":\"buildings/index.json\"}}";
		Files.write( directory.resolve( "buildings" ).resolve( "index.json" ), index.getBytes( StandardCharsets.UTF_8 ) );
		Files.write( directory.resolve( "result.json" ), result.getBytes( StandardCharsets.UTF_8 ) );
	}

	private static LoopL<Point3d> rectangle( boolean reverse, int start ) {
		Point3d[] points = {
				new Point3d( 0, 0, 0 ), new Point3d( 10, 0, 0 ),
				new Point3d( 10, 0, 5 ), new Point3d( 0, 0, 5 ) };
		LoopL<Point3d> result = new LoopL<>();
		Loop<Point3d> loop = result.newLoop();
		for ( int offset = 0; offset < points.length; offset++ ) {
			int index = reverse
					? ( start - offset + points.length ) % points.length
					: ( start + offset ) % points.length;
			loop.append( new Point3d( points[ index ] ) );
		}
		return result;
	}
}
