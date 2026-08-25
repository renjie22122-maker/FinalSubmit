package org.twak.tweed.gen;

import java.io.File;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

import javax.vecmath.Point3d;

import org.twak.utils.collections.Loop;
import org.twak.utils.collections.LoopL;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

/** Offline contract checks; starts no process and performs no network access. */
public final class SelectedBlockPanoramaServiceValidation {
	public static void main( String[] args ) throws Exception {
		if ( args.length == 1 && "--timeout-child".equals( args[ 0 ] ) ) {
			Thread.sleep( 30_000 );
			return;
		}
		Path workspace = Files.createTempDirectory( "block-pano-workspace-" );
		Path block = Files.createDirectories( workspace.resolve( "generated_blocks" ).resolve( "one" ) );
		Loop<Point3d> footprint = new Loop<>();
		footprint.append( new Point3d( 10, 0, 20 ) );
		footprint.append( new Point3d( 30, 0, 20 ) );
		footprint.append( new Point3d( 30, 0, 40 ) );
		footprint.append( new Point3d( 10, 0, 40 ) );
		LoopL<Point3d> footprints = new LoopL<>();
		footprints.add( footprint );

		SelectedBlockPanoramaService.Request request =
				new SelectedBlockPanoramaService.Request( workspace.toFile(), block.toFile(), footprints );
		Files.createDirectories( request.panoramaFolder.toPath() );
		Files.write( request.liveTodoFile.toPath(), "existing-live-plan\n".getBytes( StandardCharsets.UTF_8 ) );
		SelectedBlockPanoramaService.writeRequestAtomically( request );
		if ( !"existing-live-plan\n".equals( new String(
				Files.readAllBytes( request.liveTodoFile.toPath() ), StandardCharsets.UTF_8 ) ) )
			throw new AssertionError( "request preparation changed the live workspace todo.list" );
		JsonNode json = new ObjectMapper().readTree( request.requestFile );
		if ( json.path( "schema_version" ).asInt() != 1 ||
				!"myProject.block_panoramas.request".equals( json.path( "kind" ).asText() ) )
			throw new AssertionError( "wrong selected-block panorama request contract" );
		if ( !workspace.toFile().getCanonicalPath().equals( json.path( "workspace" ).asText() ) )
			throw new AssertionError( "request workspace is not canonical" );
		if ( json.path( "footprints" ).size() != 1 ||
				json.path( "footprints" ).get( 0 ).path( "points" ).size() != 4 )
			throw new AssertionError( "request does not preserve only the selected footprint" );
		if ( json.has( "data_builder" ) || json.has( "panorama" ) || json.has( "labels" ) )
			throw new AssertionError( "legacy panorama inputs leaked into the request" );

		File conda = new File( "conda.exe" ), bridge = new File( "bridge/run.py" );
		List<String> plan = SelectedBlockPanoramaService.planCommand( conda, "sat3dgen", bridge,
				request.requestFile, request.todoFile, request.planReport );
		List<String> sample = SelectedBlockPanoramaService.sampleCommand( conda, "sat3dgen", bridge,
				request.todoFile, request.panoramaFolder, request.sampleReport );
		List<String> batch = SelectedBlockPanoramaService.batchCommand( conda, "sat3dgen", bridge,
				request.todoFile, request.panoramaFolder, request.batchReport, request.sampleReport );
		List<String> promotion = SelectedBlockPanoramaService.promotionCommand( conda, "sat3dgen", bridge,
				request.requestFile, request.todoFile, request.planReport, request.batchReport,
				request.promotionReport );
		if ( !plan.contains( "prepare-block-panos" ) || !sample.contains( "import-streetview-panos" ) )
			throw new AssertionError( "wrong Python CLI entry point" );
		if ( !plan.contains( "--todo" ) || !request.todoFile.getParentFile().equals( request.referenceDirectory ) ||
				request.todoFile.equals( request.liveTodoFile ) )
			throw new AssertionError( "planner does not use a selection-scoped todo.list" );
		if ( sample.contains( "--all" ) || sample.contains( "--sample-approved" ) )
			throw new AssertionError( "sample command bypasses the one-record safety gate" );
		if ( !batch.contains( "--all" ) || !batch.contains( "--sample-approved" ) ||
				!batch.contains( "--sample-report" ) )
			throw new AssertionError( "batch command bypasses explicit sample approval" );
		if ( !promotion.contains( "promote-block-panos" ) || !promotion.contains( "--plan-report" ) ||
				!promotion.contains( "--batch-report" ) )
			throw new AssertionError( "approved plan promotion lacks its transactional evidence" );
		for ( String token : promotion )
			if ( token.toLowerCase().contains( "api_key" ) || token.startsWith( "AIza" ) )
				throw new AssertionError( "API credential leaked into argv" );

		List<String> child = new ArrayList<>( Arrays.asList(
				new File( new File( System.getProperty( "java.home" ), "bin" ),
						System.getProperty( "os.name" ).toLowerCase().contains( "win" ) ? "java.exe" : "java" )
						.getAbsolutePath(),
				"-cp", System.getProperty( "java.class.path" ),
				SelectedBlockPanoramaServiceValidation.class.getName(), "--timeout-child" ) );
		long started = System.nanoTime();
		try {
			SelectedBlockPanoramaService.runChecked( child,
					new File( System.getProperty( "user.dir", "." ) ).getCanonicalFile(),
					request.referenceDirectory.toPath().resolve( "timeout.log" ).toFile(),
					request.referenceDirectory.toPath().resolve( "timeout-report.json" ).toFile(),
					1, "Validation child" );
			throw new AssertionError( "bounded process wait did not time out" );
		} catch ( IOException expected ) {
			if ( !expected.getMessage().contains( "timed out" ) )
				throw expected;
		}
		long elapsedSeconds = ( System.nanoTime() - started ) / 1_000_000_000L;
		if ( elapsedSeconds > 10 )
			throw new AssertionError( "timed-out child was not reclaimed promptly" );

		System.out.println( "Selected-block panorama staged-todo/argv/timeout validation passed" );
	}
}
